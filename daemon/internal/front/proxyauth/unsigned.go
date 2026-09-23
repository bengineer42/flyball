package proxyauth

import (
	"crypto/sha256"
	"crypto/subtle"
	"fmt"
	"log/slog"
	"mime"
	"net"
	"net/http"
	"net/netip"
	"net/textproto"
	"os"
	"strconv"
	"strings"
	"sync"

	"flyballd/internal/front"
)

// headerSet is an unsigned preset: which headers carry what.
type headerSet struct {
	issuer string // the namespace: sub = proxy:<issuer>#<subject>
	user   string // the subject
	groups string // "" = none
	sep    string
	name   string // display only; "" = none
	// extra are the proxy's other headers: never read, but a non-canonical
	// or underscore copy of one is refused like the ones read.
	extra []string
	// email, if set, is a header carrying the user's email: a user equal
	// to it is an email used as the subject (oauth2-proxy
	// --prefer-email-to-user), refused.
	email   string
	qDecode bool // the name may be RFC 2047 encoded (Tailscale)
}

type unsignedClient struct {
	hs     headerSet
	unix   bool
	peers  []netip.Prefix
	secret *[sha256.Size]byte // nil: no secret_file
	o      Options
	// spellings maps a header name, lower-cased with '_' as '-', to the
	// canonical spelling, for every header this preset trusts.
	spellings map[string]string

	mu   sync.Mutex
	seen map[string]bool // uid + subject pairs already recorded
}

func newUnsigned(c *front.ProxyConfig, p front.Plan, o Options, hs headerSet) (front.Client, error) {
	u := &unsignedClient{hs: hs, o: o, spellings: map[string]string{}, seen: map[string]bool{}}
	from := []string(c.From)
	if len(from) == 0 {
		from = []string{"unix"}
	}
	listenUnix := strings.HasPrefix(p.Listen, "unix:")
	needsSecret := false
	if from[0] == "unix" {
		if len(from) > 1 {
			return nil, fmt.Errorf("from: unix stands alone; it cannot be mixed with peer addresses")
		}
		if !listenUnix {
			return nil, fmt.Errorf("preset %s trusts unsigned headers only from the front's unix socket (from: unix): set listen: unix:/path and point the proxy at it, or list the proxy's address in from: (a loopback one also needs secret_file:)", c.Preset)
		}
		u.unix = true
	} else {
		if listenUnix {
			return nil, fmt.Errorf("from: lists peer addresses, but the front listens on a unix socket; use from: unix")
		}
		local, err := o.LocalAddrs()
		if err != nil {
			needsSecret = true // cannot tell which addresses are this host's: assume any may be
		}
		for _, s := range from {
			pr, err := parsePrefix(s)
			if err != nil {
				return nil, fmt.Errorf("from: %q is not unix, an IP or a CIDR", s)
			}
			if pr.Bits() == 0 {
				return nil, fmt.Errorf("from: %s trusts every peer; name the proxy's address", s)
			}
			if pr.Overlaps(loopback4) || pr.Contains(netip.IPv6Loopback()) {
				needsSecret = true
			}
			for _, a := range local {
				if pr.Contains(a) {
					needsSecret = true
				}
			}
			u.peers = append(u.peers, pr)
		}
	}
	if c.SecretFile != "" {
		s, err := readSecret(c.SecretFile)
		if err != nil {
			return nil, err
		}
		sum := sha256.Sum256(s)
		u.secret = &sum
	}
	if needsSecret && u.secret == nil {
		return nil, fmt.Errorf("from: %s includes a loopback or this host's own address, which means every local process (F15); add secret_file: and have the proxy send it as %s",
			strings.Join(from, ", "), SecretHeader)
	}
	if u.secret == nil {
		// A range is allowed (a proxy in a container network changes
		// address), but anything wider than one host lets every host in it
		// assert any identity: say so once, at start (sec F3).
		var wide []string
		for _, pr := range u.peers {
			if !pr.IsSingleIP() {
				wide = append(wide, pr.String())
			}
		}
		if len(wide) > 0 {
			o.Logger.Warn(fmt.Sprintf("proxy from: %s is more than one host, and every host in it can assert any identity"+
				" to this front; name the proxy's own address, or add secret_file:", strings.Join(wide, ", ")))
		}
	}
	for _, name := range append([]string{hs.user, hs.groups, hs.name, hs.email, SecretHeader}, hs.extra...) {
		if name != "" {
			canon := textproto.CanonicalMIMEHeaderKey(name)
			u.spellings[normalise(canon)] = canon
		}
	}
	return u, nil
}

var loopback4 = netip.MustParsePrefix("127.0.0.0/8")

func (u *unsignedClient) Name() string { return ProviderName }

func (u *unsignedClient) Authenticate(r *http.Request) (front.Identity, front.Outcome, error) {
	if !u.vouched(r) {
		return front.Identity{}, front.NotMine, nil
	}
	if u.secret != nil {
		got := r.Header.Values(SecretHeader)
		switch {
		case len(got) == 0:
			return front.Identity{}, front.NotMine, nil // not vouched for: ignored
		case len(got) > 1:
			return front.Identity{}, front.Reject, nil
		}
		sum := sha256.Sum256([]byte(got[0]))
		if subtle.ConstantTimeCompare(sum[:], u.secret[:]) != 1 {
			return front.Identity{}, front.Reject, nil
		}
	}
	// A copy of a trusted header under another spelling (Remote_User,
	// Remote_groups) is a client's, passed through by the proxy: refuse the
	// request rather than guess which the proxy meant.
	for k := range r.Header {
		if canon, ok := u.spellings[normalise(k)]; ok && k != canon {
			return front.Identity{}, front.Reject, nil
		}
	}

	users := r.Header.Values(u.hs.user)
	if len(users) == 0 {
		if u.present(r, u.hs.groups) || u.present(r, u.hs.name) {
			return front.Identity{}, front.Reject, nil // parts of an identity, without its subject
		}
		return front.Identity{}, front.NotMine, nil
	}
	if len(users) > 1 {
		return front.Identity{}, front.Reject, nil
	}
	user := strings.TrimSpace(users[0])
	if user == "" || !cleanID(user) {
		return front.Identity{}, front.Reject, nil
	}
	if u.hs.email != "" {
		for _, e := range r.Header.Values(u.hs.email) {
			if strings.EqualFold(strings.TrimSpace(e), user) {
				return front.Identity{}, front.Reject, nil // the email as the subject (F17)
			}
		}
	}
	id := front.Identity{Issuer: u.hs.issuer, Subject: user}
	if u.hs.groups != "" {
		id.Groups = splitGroups(r.Header.Values(u.hs.groups), u.hs.sep)
	}
	if u.hs.name != "" {
		if names := r.Header.Values(u.hs.name); len(names) == 1 {
			n := strings.TrimSpace(names[0])
			if u.hs.qDecode && strings.HasPrefix(n, "=?") {
				if d, err := new(mime.WordDecoder).DecodeHeader(n); err == nil {
					n = d
				}
			}
			if cleanID(n) {
				id.Name = n
			}
		}
	}
	if u.unix {
		u.record(r, id)
	}
	return id, front.Accept, nil
}

// vouched: the request came from the front's unix socket (from: unix) or
// from a listed TCP peer -- the connection's peer, never X-Forwarded-For.
func (u *unsignedClient) vouched(r *http.Request) bool {
	if u.unix {
		_, ok := r.Context().Value(http.LocalAddrContextKey).(*net.UnixAddr)
		return ok
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return false
	}
	a, err := netip.ParseAddr(host)
	if err != nil {
		return false
	}
	a = a.Unmap()
	for _, p := range u.peers {
		if p.Contains(a) {
			return true
		}
	}
	return false
}

func (u *unsignedClient) present(r *http.Request, name string) bool {
	return name != "" && len(r.Header.Values(name)) > 0
}

// record writes proxy.peer the first time a uid presents a subject: which
// local account's process vouched for whom (SO_PEERCRED).
func (u *unsignedClient) record(r *http.Request, id front.Identity) {
	uid := "unknown" // the server was not given ConnContext
	if n, ok := PeerUID(r.Context()); ok {
		uid = strconv.FormatUint(uint64(n), 10)
	}
	sub := ProviderName + ":" + id.Issuer + "#" + id.Subject
	u.mu.Lock()
	key := uid + "\x00" + sub
	first := !u.seen[key]
	if first {
		if len(u.seen) >= 4096 {
			clear(u.seen)
		}
		u.seen[key] = true
	}
	u.mu.Unlock()
	if !first {
		return
	}
	attrs := []slog.Attr{slog.String("uid", uid), slog.String("sub", sub), slog.String("preset", u.hs.issuer)}
	if u.o.Audit != nil {
		u.o.Audit.Event("proxy.peer", attrs...)
		return
	}
	u.o.Logger.LogAttrs(r.Context(), slog.LevelInfo, "proxy.peer", attrs...)
}

// normalise is a header name lower-cased with '_' as '-'.
func normalise(k string) string { return strings.ReplaceAll(strings.ToLower(k), "_", "-") }

// readSecret reads secret_file: at least 16 bytes after trailing
// whitespace, and not readable by every user.
func readSecret(path string) ([]byte, error) {
	fi, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("secret_file: %v", err)
	}
	if fi.Mode().Perm()&0o004 != 0 {
		return nil, fmt.Errorf("secret_file %s is readable by every user; chmod o-r it", path)
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("secret_file: %v", err)
	}
	s := strings.TrimRight(string(b), " \t\r\n")
	if len(s) < 16 {
		return nil, fmt.Errorf("secret_file %s: the secret must be at least 16 characters", path)
	}
	return []byte(s), nil
}

func parsePrefix(s string) (netip.Prefix, error) {
	if strings.Contains(s, "/") {
		p, err := netip.ParsePrefix(s)
		if err != nil {
			return p, err
		}
		if p.Addr().Is4In6() && p.Bits() >= 96 {
			p = netip.PrefixFrom(p.Addr().Unmap(), p.Bits()-96)
		}
		return p.Masked(), nil
	}
	a, err := netip.ParseAddr(s)
	if err != nil {
		return netip.Prefix{}, err
	}
	a = a.Unmap()
	return netip.PrefixFrom(a, a.BitLen()), nil
}

// interfaceAddrs is this host's own addresses (a docker bridge's gateway
// among them).
func interfaceAddrs() ([]netip.Addr, error) {
	as, err := net.InterfaceAddrs()
	if err != nil {
		return nil, err
	}
	var out []netip.Addr
	for _, a := range as {
		if n, ok := a.(*net.IPNet); ok {
			if ip, ok := netip.AddrFromSlice(n.IP); ok {
				out = append(out, ip.Unmap())
			}
		}
	}
	return out, nil
}
