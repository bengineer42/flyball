package front

import (
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"strings"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/exposure"
	"flyballd/internal/front/store"
	"flyballd/internal/front/tlsfile"
	"flyballd/internal/grants"

	"gopkg.in/yaml.v3"
)

// DefaultListen is where a front listens when its config names nowhere.
const DefaultListen = "127.0.0.1:8000"

// The shapes (auth.md § Shapes). sso is Phase 3: in Phase 1 it falls back.
const (
	ShapeLocal    = "local"
	ShapePassword = "password"
	ShapeProxy    = "proxy"
	ShapeSSO      = "sso"
)

// Config is the front's configuration: `runner.front` in a rig file (for
// `flyball run`), and the top level of flyballd.yaml. The Python model is
// FrontConfig in engine/src/flyball/runtime/config.py; the keys match.
type Config struct {
	Listen    string       `yaml:"listen"`
	Auth      string       `yaml:"auth"` // local | password | proxy | sso
	URL       string       `yaml:"url"`
	TLS       *TLSFiles    `yaml:"tls"`
	Password  string       `yaml:"password"` // must be a $scrypt$ line; plaintext → fallback
	Anonymous string       `yaml:"anonymous"`
	Proxy     *ProxyConfig `yaml:"proxy"`
	// Tokens tightens the named-token lifetime ceilings (store.Lifetimes);
	// nil: the built-ins (store.DefaultLifetimes).
	Tokens *TokensConfig `yaml:"tokens"`
	// Reference-only keys.
	Session        string   `yaml:"session"`         // idle session lifetime, "12h" / "2d"
	TrustedProxies []string `yaml:"trusted_proxies"` // peers whose X-Forwarded-For names the client
}

// TokensConfig is `tokens:`: `runner.front.tokens` in a rig file (for
// `flyball run`), or flyballd.yaml's top-level `tokens:`. Both fields are
// duration strings (parseDuration: Go durations plus a `d` suffix for
// days, e.g. "90d", "36h"); "" means "not set, use the built-in". The
// Python model is TokensConfig in engine/src/flyball/runtime/config.py,
// which shapes but does not validate this block -- ResolveLifetimes does.
type TokensConfig struct {
	// DefaultLifetime is used when a token is created without an explicit
	// expires_in. Built-in: store.TokenLifetimeDefault (90 days).
	DefaultLifetime string `yaml:"default_lifetime"`
	// MaxLifetime is the hard cap for tokens that are neither cleartext
	// nor kind agent. It may only tighten the built-in ceiling
	// (store.TokenLifetimeMax, 365 days), never loosen it.
	MaxLifetime string `yaml:"max_lifetime"`
}

// TLSFiles is `tls: {cert, key}` (D-033).
type TLSFiles struct {
	Cert string `yaml:"cert"`
	Key  string `yaml:"key"`
}

// ProxyConfig is `proxy:` (auth.md § Trusted header). The front reads only
// Grants; the rest is for the preset package (daemon/internal/front/
// proxyauth, C4), which the caller hands to ResolveWith as a
// ProxyFactory. It lives here, not in proxyauth, so that proxyauth can
// import this package's Identity/Client without an import cycle.
type ProxyConfig struct {
	Preset       string              `yaml:"preset"`
	From         StringList          `yaml:"from"` // "unix", or peer IPs/CIDRs
	SecretFile   string              `yaml:"secret_file"`
	Team         string              `yaml:"team"`
	Issuer       string              `yaml:"issuer"`
	Audience     string              `yaml:"audience"`
	Grants       map[string][]string `yaml:"grants"` // grant name (pending D-034) -> subjects / "group:<id>"
	UserHeader   string              `yaml:"user_header"`
	GroupsHeader string              `yaml:"groups_header"`
	Separator    string              `yaml:"separator"`
	JWT          *CustomJWT          `yaml:"jwt"`
}

// CustomJWT is the `custom` preset's `jwt:` block.
type CustomJWT struct {
	Header     string   `yaml:"header"`
	JWKSURL    string   `yaml:"jwks_url"`
	Issuer     string   `yaml:"issuer"`
	Audience   string   `yaml:"audience"`
	Algorithms []string `yaml:"algorithms"`
}

// StringList is a YAML scalar or sequence of strings (`from: unix` or
// `from: [10.0.0.0/8]`).
type StringList []string

// UnmarshalYAML accepts a scalar as a one-element list.
func (s *StringList) UnmarshalYAML(n *yaml.Node) error {
	if n.Kind == yaml.ScalarNode {
		*s = StringList{n.Value}
		return nil
	}
	var list []string
	if err := n.Decode(&list); err != nil {
		return err
	}
	*s = list
	return nil
}

// ProxyFactory builds the proxy shape's provider from `proxy:` and the plan
// so far (Listen, URL). An error refuses the shape: the front falls back
// to local on loopback (D-028), with the error as the banner's reason.
type ProxyFactory func(c *ProxyConfig, p Plan) (Client, error)

// Plan is what the front actually serves, after every D-028 fallback.
type Plan struct {
	Shape     string // the shape actually served
	Requested string // the listen address asked for
	Listen    string // after the D-028 loopback fallback: host:port, or unix:/path
	Fallback  string // "" or why the front fell back to local-on-loopback (banner, /api/auth, audit)
	// Refused is the address a credential shape asked for, after its D-028
	// fallback: the front answers 503 there (New holds it), and serves the
	// local shape on a fresh loopback address (Listen) instead -- a reverse
	// proxy pointed at the requested address must not reach an open
	// console. "" otherwise.
	Refused string
	// RefusedTLS: Refused is answered over TLS with it -- the tls: pair
	// asked for, where it loads. nil: plain HTTP.
	RefusedTLS *tlsfile.Reloader
	Warnings   []string // cleartext, open, ignored reference keys, ...
	HostAllow  []string // nil = any Host (credential shapes without url:); loopback names match any port
	// HostKnown: HostAllow also takes an IP address and this machine's own
	// names (exposure.KnownHost) -- the local shape served beyond loopback
	// by --insecure-open, never another DNS name (D-043).
	HostKnown   bool
	OriginAllow []string // the url: origin, if any; same-site-with-Host is always allowed
	Secure      bool     // cookie Secure / __Host-

	Anonymous   string              // "none" | "read"
	Password    string              // the $scrypt$ line (shape password)
	URL         *url.URL            // url:, parsed; nil when unset
	TLS         *tlsfile.Reloader   // non-nil: serve TLS with it
	SessionIdle time.Duration       // 0: the store's default
	Trusted     []netip.Prefix      // trusted_proxies
	Grants      map[string][]string // proxy.grants
	Lifetimes   store.Lifetimes     // tokens: default_lifetime/max_lifetime, resolved (store.DefaultLifetimes if unset)
}

// Banner is the lines to print at start: the fallback, then the warnings.
func (p Plan) Banner() string {
	var lines []string
	switch {
	case p.Refused != "":
		fresh := ""
		if strings.HasSuffix(p.Listen, ":0") {
			fresh = " (a fresh port: the line saying where it serves names it)"
		}
		lines = append(lines, fmt.Sprintf("front: %s -- %s answers 503 (auth misconfigured), and the local shape is served on %s only%s; the rig keeps running (D-028)",
			p.Fallback, p.Refused, p.Listen, fresh))
	case p.Fallback != "":
		lines = append(lines, fmt.Sprintf("front: %s -- serving the local shape on %s only; the rig keeps running (D-028)", p.Fallback, p.Listen))
	}
	for _, w := range p.Warnings {
		lines = append(lines, "front: "+w)
	}
	return strings.Join(lines, "\n")
}

// Close stops the TLS reloaders, if any.
func (p Plan) Close() {
	if p.TLS != nil {
		p.TLS.Close()
	}
	if p.RefusedTLS != nil {
		p.RefusedTLS.Close()
	}
}

// Resolve is ResolveWith without proxy presets: a proxy shape falls back.
func Resolve(c Config, insecureOpen bool) Plan {
	p, _ := ResolveWith(c, insecureOpen, nil)
	return p
}

// loopbackNames are the Host names the local shape accepts, on any port
// (exposure.LoopbackName's rule).
var loopbackNames = []string{"localhost", "127.0.0.1", "[::1]"}

// ResolveWith decides what the front serves. It never fails: every error in
// c is a fallback to the local shape on loopback, with the reason in
// Plan.Fallback. When c asked for a credential shape (anything but local),
// the fallback does not serve the local shape where c asked to listen
// (Plan.Refused: answered 503) but on a fresh loopback address. insecureOpen
// is the per-invocation --insecure-open. The returned Client is the proxy
// shape's provider (nil otherwise).
func ResolveWith(c Config, insecureOpen bool, proxy ProxyFactory) (Plan, Client) {
	p := Plan{Shape: c.Auth, Requested: c.Listen, Listen: c.Listen, Anonymous: "none"}
	if p.Shape == "" {
		p.Shape = ShapeLocal
	}
	if p.Listen == "" {
		p.Listen = DefaultListen
	}
	if p.Requested == "" {
		p.Requested = p.Listen
	}
	var client Client
	credentials := p.Shape != ShapeLocal // what was asked for; p.Shape changes on a fallback
	listenOK := true
	fallback := func(format string, args ...any) {
		if p.Fallback != "" {
			return
		}
		p.Fallback = fmt.Sprintf(format, args...)
		if credentials && listenOK {
			// A proxy (or a TLS-terminating one in front of password) still
			// forwards to the requested address: never an open console there.
			p.Refused = p.Listen
			p.Shape, p.Listen = ShapeLocal, freshLoopback(p.Listen)
		} else {
			p.Shape, p.Listen = ShapeLocal, loopbackOf(p.Listen)
		}
		p.URL, p.Secure, p.Password, p.Grants, client = nil, false, "", nil, nil
		if p.TLS != nil {
			p.TLS.Close()
			p.TLS = nil
		}
	}

	if !isUnix(p.Listen) {
		if _, _, err := net.SplitHostPort(p.Listen); err != nil {
			p.Listen, listenOK = DefaultListen, false
			fallback("listen %q is not host:port or unix:/path", c.Listen)
		}
	}
	if c.URL != "" {
		u, err := parseURL(c.URL)
		if err != nil {
			fallback("url: %v", err)
		} else {
			p.URL = u
		}
	}

	switch p.Shape {
	case ShapeLocal:
		if !loopbackListen(p.Listen) {
			if insecureOpen {
				p.Warnings = append(p.Warnings, exposure.OpenWarning(p.Listen))
			} else {
				fallback("auth: local (no login) serves loopback only, and %s is not loopback; to serve it"+
					" on the network choose auth: password or proxy, or, knowingly, --insecure-open", p.Listen)
			}
		}
	case ShapePassword:
		if c.Password == "" {
			fallback("auth: password needs a password: line (`flyball password` makes one)")
		} else if err := store.ParseScrypt(c.Password); err != nil {
			fallback("the password is not a $scrypt$ line (%v); plaintext passwords are refused -- `flyball password` makes one", err)
		} else {
			p.Password = c.Password
		}
	case ShapeProxy:
		switch {
		case c.Proxy == nil:
			fallback("auth: proxy needs a proxy: block")
		case proxy == nil:
			fallback("auth: proxy: this build has no proxy presets")
		default:
			p.Grants = c.Proxy.Grants
			for _, name := range grants.UnknownGrants(p.Grants) {
				p.Warnings = append(p.Warnings, fmt.Sprintf("proxy.grants: %q is not a grant in the vocabulary; it grants nothing", name))
			}
		}
	case ShapeSSO:
		fallback("auth: sso is not in this release; use auth: proxy with the oauth2-proxy preset")
	default:
		fallback("auth: %q is not a shape (local, password, proxy)", c.Auth)
	}

	if p.Fallback == "" && c.TLS != nil {
		r, err := tlsfile.NewReloader(c.TLS.Cert, c.TLS.Key)
		if err != nil {
			fallback("tls: %v", err)
		} else {
			p.TLS = r
		}
	}
	if p.Fallback == "" && p.Shape == ShapeProxy {
		cl, err := proxy(c.Proxy, p)
		if err != nil {
			fallback("proxy: %v", err)
		} else {
			client = cl
		}
	}

	// The refused listen keeps the TLS it was asked for when the pair
	// loads, so a TLS client (a browser, an https upstream) reads its 503.
	// When TLS itself is what failed, plain HTTP is the only way left to
	// answer there.
	if p.Refused != "" && c.TLS != nil {
		if r, err := tlsfile.NewReloader(c.TLS.Cert, c.TLS.Key); err == nil {
			p.RefusedTLS = r
		}
	}

	switch c.Anonymous {
	case "", "none":
	case "read":
		p.Anonymous = "read"
	default:
		p.Warnings = append(p.Warnings, fmt.Sprintf("anonymous: %q is not none or read; using none", c.Anonymous))
	}
	if c.Session != "" {
		d, err := store.ParseDuration(c.Session)
		if err != nil || d <= 0 {
			p.Warnings = append(p.Warnings, fmt.Sprintf("session: %q is not a duration like 12h or 7d; using the default", c.Session))
		} else {
			p.SessionIdle = d
		}
	}
	for _, s := range c.TrustedProxies {
		pr, err := parsePrefix(s)
		if err != nil {
			p.Warnings = append(p.Warnings, fmt.Sprintf("trusted_proxies: %q is not an IP or CIDR; ignored", s))
			continue
		}
		p.Trusted = append(p.Trusted, pr)
	}
	var defaultLifetime, maxLifetime string
	if c.Tokens != nil {
		defaultLifetime, maxLifetime = c.Tokens.DefaultLifetime, c.Tokens.MaxLifetime
	}
	var lifetimeWarnings []string
	p.Lifetimes, lifetimeWarnings = store.ResolveLifetimes(defaultLifetime, maxLifetime)
	p.Warnings = append(p.Warnings, lifetimeWarnings...)

	if p.Shape != ShapeLocal && !loopbackListen(p.Listen) && p.TLS == nil {
		p.Warnings = append(p.Warnings, exposure.CleartextWarning(p.Listen))
	}
	p.Secure = p.TLS != nil || (p.URL != nil && p.URL.Scheme == "https")

	if p.Shape == ShapeLocal || p.URL != nil {
		if p.URL != nil {
			p.HostAllow = append(p.HostAllow, urlAuthority(p.URL))
		}
		p.HostAllow = append(p.HostAllow, loopbackNames...)
		// --insecure-open: every caller is the console, so on every route
		// the Host is a name no page elsewhere can own, or url:'s (D-043).
		p.HostKnown = p.Shape == ShapeLocal && !loopbackListen(p.Listen)
	}
	if p.URL != nil {
		p.OriginAllow = []string{originOf(p.URL)}
	}
	return p, client
}

func isUnix(listen string) bool { return strings.HasPrefix(listen, "unix:") }

// loopbackListen: the listen address reaches this machine only (a unix
// socket counts).
func loopbackListen(listen string) bool { return isUnix(listen) || exposure.IsLoopback(listen) }

// loopbackOf is listen moved to 127.0.0.1, keeping its port.
func loopbackOf(listen string) string {
	if loopbackListen(listen) {
		return listen
	}
	_, port, err := net.SplitHostPort(listen)
	if err != nil {
		return DefaultListen
	}
	return net.JoinHostPort("127.0.0.1", port)
}

// freshLoopback is where a credential shape's fallback serves the local
// shape: a socket beside a unix one (same directory, so the same
// permissions), else a fresh port on 127.0.0.1.
func freshLoopback(listen string) string {
	if path, ok := strings.CutPrefix(listen, "unix:"); ok && len(path)+len(".local") <= endpoint.MaxSocketPath {
		return "unix:" + path + ".local"
	}
	return "127.0.0.1:0"
}

func parseURL(s string) (*url.URL, error) {
	u, err := url.Parse(s)
	if err != nil {
		return nil, err
	}
	if (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil ||
		(u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("%q is not an http(s)://host[:port] URL", s)
	}
	u.Host = strings.ToLower(u.Host)
	return u, nil
}

// urlAuthority is host:port with the scheme's default port filled in.
func urlAuthority(u *url.URL) string {
	port := u.Port()
	if port == "" {
		port = map[string]string{"http": "80", "https": "443"}[u.Scheme]
	}
	host := u.Hostname()
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	return host + ":" + port
}

// originOf is the URL's origin as a browser sends it: the default port
// left out.
func originOf(u *url.URL) string {
	a := urlAuthority(u)
	if d := map[string]string{"http": ":80", "https": ":443"}[u.Scheme]; strings.HasSuffix(a, d) {
		a = strings.TrimSuffix(a, d)
	}
	return u.Scheme + "://" + a
}

func parsePrefix(s string) (netip.Prefix, error) {
	if strings.Contains(s, "/") {
		p, err := netip.ParsePrefix(s)
		return p.Masked(), err
	}
	a, err := netip.ParseAddr(s)
	if err != nil {
		return netip.Prefix{}, err
	}
	return netip.PrefixFrom(a.Unmap(), a.Unmap().BitLen()), nil
}
