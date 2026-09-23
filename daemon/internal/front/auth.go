package front

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"math"
	"net"
	"net/http"
	"net/netip"
	"slices"
	"strconv"
	"strings"
	"time"

	"flyballd/internal/exposure"
	"flyballd/internal/front/store"
	"flyballd/internal/grants"
)

// The schemes: how a caller got in (AuthInfo.scheme).
const (
	SchemeLocal     = "local"
	SchemeAnonymous = "anonymous"
	SchemeSession   = "session"
	SchemeToken     = "token"
	SchemeProxy     = "proxy"
)

// The front's own subjects (auth.md § The signed principal).
const (
	SubConsole   = "local:console" // the local shape
	SubAdmin     = "local:admin"   // the admin password
	SubAnonymous = "anon:"
	SubProbe     = "front:probe"
)

// Caller is who a request is, after the provider chain.
type Caller struct {
	Scheme string // SchemeLocal ... SchemeProxy
	Sub    string
	Name   string
	Kind   string // human | service | agent
	Sid    string // the revocation key: a session's sid, a token's id
	// Scopes is the ceiling, as stored scopes (<verb>:<rig|*>, and the
	// management scope for a token that has it).
	Scopes []string
	// Issuer is, for a token, the sub of whoever created it ("" = the CLI).
	Issuer string
}

// authError is a terminal refusal from the chain.
type authError struct {
	status      int // 401 or 503
	detail      string
	clearCookie bool
}

func (e *authError) Error() string { return e.detail }

// Authenticate runs the provider chain (bearer token → session cookie →
// proxy assertion → anonymous) on r. A presented credential that does not
// work is an error, never anonymous (F10): its Status is 401, or 503 when
// the store or the IdP could not answer. flyballd's management routes use
// it (C1): a management call needs Scheme == SchemeToken and
// grants.HasManagement(Scopes); a session never has it.
func (f *Front) Authenticate(r *http.Request) (Caller, error) {
	return f.authenticate(r, false)
}

// Status is the HTTP status for an Authenticate error (401 or 503).
func Status(err error) int {
	var ae *authError
	if errors.As(err, &ae) {
		return ae.status
	}
	return http.StatusInternalServerError
}

// authenticate: lenient is GET /api/auth, where a stale cookie is answered
// as anonymous (with the cookie cleared) instead of refused.
func (f *Front) authenticate(r *http.Request, lenient bool) (Caller, error) {
	auth := r.Header.Values("Authorization")
	presented := len(auth) > 0
	if len(auth) > 1 {
		return Caller{}, &authError{status: 401, detail: "Send one Authorization header"}
	}
	if presented {
		scheme, cred, _ := strings.Cut(auth[0], " ")
		cred = strings.TrimSpace(cred)
		if strings.EqualFold(scheme, "Bearer") && store.IsToken(cred) {
			return f.bearer(r, cred)
		}
	}
	if f.plan.Shape == ShapePassword {
		if ck, err := r.Cookie(f.cookie); err == nil && ck.Value != "" {
			if s, ok := f.sessions.Lookup(ck.Value); ok {
				return Caller{Scheme: SchemeSession, Sub: s.Subject, Name: "admin", Kind: store.KindHuman,
					Sid: s.Sid, Scopes: s.Scopes}, nil
			}
			if !lenient || presented {
				return Caller{}, &authError{status: 401, detail: "Signed out; sign in again", clearCookie: true}
			}
			c := f.anonymous()
			c.Scheme = SchemeAnonymous
			return c, &staleCookie{}
		}
	}
	if f.plan.Shape == ShapeProxy && f.o.Proxy != nil {
		id, out, err := f.o.Proxy.Authenticate(r)
		switch {
		case err != nil:
			f.o.Audit.Event("proxy.refused", slog.String("peer", f.peer(r)), slog.String("err", err.Error()))
			return Caller{}, &authError{status: 503, detail: "The identity provider could not be reached"}
		case out == Reject:
			f.o.Audit.Event("proxy.refused", slog.String("peer", f.peer(r)))
			return Caller{}, &authError{status: 401, detail: "The proxy's assertion was refused"}
		case out == Accept:
			return f.proxyCaller(id), nil
		}
	}
	if presented {
		return Caller{}, &authError{status: 401, detail: "Unrecognised credential"}
	}
	return f.anonymous(), nil
}

// staleCookie is not a failure: GET /api/auth answers anonymous and
// clears the cookie.
type staleCookie struct{}

func (*staleCookie) Error() string { return "stale session cookie" }

func (f *Front) bearer(r *http.Request, secret string) (Caller, error) {
	if f.tokens == nil {
		return Caller{}, &authError{status: 503, detail: "Named tokens are unavailable"}
	}
	t, err := f.tokens.Lookup(secret)
	switch {
	case errors.Is(err, store.ErrNoToken), errors.Is(err, store.ErrTokenExpired):
		f.o.Audit.Event("token.refused", slog.String("peer", f.peer(r)), slog.String("why", err.Error()))
		return Caller{}, &authError{status: 401, detail: "Invalid or expired token"}
	case err != nil:
		f.log.Error("front: tokens file", "err", err)
		return Caller{}, &authError{status: 503, detail: "Named tokens are unavailable"}
	}
	return Caller{Scheme: SchemeToken, Sub: "token:" + t.Name, Name: t.Name, Kind: t.Kind, Sid: t.ID,
		Scopes: t.Scopes, Issuer: t.Issuer}, nil
}

func (f *Front) proxyCaller(id Identity) Caller {
	name := f.o.Proxy.Name()
	sub := name + ":" + id.Subject
	if id.Issuer != "" {
		sub = name + ":" + id.Issuer + "#" + id.Subject
	}
	kind := id.Kind
	if kind == "" {
		kind = store.KindHuman
	}
	sid := id.Sid
	if sid == "" {
		sid = randomHex(16)
	}
	nm := id.Name
	if nm == "" {
		nm = id.Subject
	}
	return Caller{Scheme: SchemeProxy, Sub: sub, Name: nm, Kind: kind, Sid: sid,
		Scopes: grants.Match(f.plan.Grants, id.Subject, id.Groups)}
}

// anonymous is a caller with no credential: the local shape's console, or
// the anonymous visitor.
func (f *Front) anonymous() Caller {
	if f.plan.Shape == ShapeLocal {
		return Caller{Scheme: SchemeLocal, Sub: SubConsole, Name: "local", Kind: store.KindHuman,
			Sid: f.localSid, Scopes: grants.All()}
	}
	c := Caller{Scheme: SchemeAnonymous, Sub: SubAnonymous, Kind: store.KindHuman, Sid: randomHex(16), Scopes: []string{}}
	if f.plan.Anonymous == "read" {
		c.Scopes = grants.Match(nil, "", nil) // read on every rig
	}
	return c
}

// Verbs is c's verbs on the rig named rig, sorted: the ceiling expanded
// for that rig, and, for a token, intersected with its issuer's current
// verbs (merge requirement 12).
func (f *Front) Verbs(c Caller, rig string) []string {
	v := grants.ForRig(c.Scopes, rig)
	if c.Scheme == SchemeToken {
		v = grants.Intersect(v, f.issuerVerbs(c.Issuer, rig))
	}
	return v
}

// issuerVerbs is what the identity that created a token holds now. The
// CLI (the owner of the config and the tokens file) and the front's own
// identities hold every verb; a proxy identity holds what proxy.grants
// give its subject now (group grants cannot be seen without its request:
// only subject entries count, else read); anything else holds nothing.
func (f *Front) issuerVerbs(issuer, rig string) []string {
	switch {
	case issuer == "" || strings.HasPrefix(issuer, "local:"):
		return grants.Verbs()
	case strings.HasPrefix(issuer, "proxy:"):
		rest := strings.TrimPrefix(issuer, "proxy:")
		if _, subject, ok := strings.Cut(rest, "#"); ok {
			rest = subject
		}
		return grants.ForRig(grants.Match(f.plan.Grants, rest, nil), rig)
	}
	return []string{}
}

// CheckHost is the Host allow-list (merge requirement 8): the local shape
// answers loopback names only (DNS rebinding), and url: adds its own host.
func (f *Front) CheckHost(r *http.Request) bool {
	if f.plan.HostAllow == nil {
		return true
	}
	defPort := "80"
	if f.plan.URL != nil && f.plan.URL.Scheme == "https" {
		defPort = "443"
	}
	host := strings.ToLower(r.Host)
	withPort := host
	if _, _, err := net.SplitHostPort(host); err != nil {
		withPort = net.JoinHostPort(strings.Trim(host, "[]"), defPort)
	}
	for _, allowed := range f.plan.HostAllow {
		if slices.Contains(loopbackNames, allowed) {
			if exposure.LoopbackName(host) {
				return true
			}
			continue
		}
		if withPort == allowed {
			return true
		}
	}
	return false
}

// CheckOrigin is the Origin rule for a request that acts (merge
// requirement 9): exactly one Origin, not "null", same-site with the
// request's Host or the url: origin; with no Origin at all, only
// `Sec-Fetch-Site: same-origin`. A bearer token is exempt (the caller
// decides that); everything else is checked.
func (f *Front) CheckOrigin(r *http.Request) bool {
	origins := r.Header.Values("Origin")
	if len(origins) == 0 {
		return r.Header.Get("Sec-Fetch-Site") == "same-origin"
	}
	if len(origins) > 1 || origins[0] == "null" {
		return false
	}
	o := strings.ToLower(origins[0])
	if slices.Contains(f.plan.OriginAllow, o) {
		return true
	}
	scheme := "http"
	if r.TLS != nil {
		scheme = "https"
	}
	return exposure.SameSite(origins[0], r.Host, scheme)
}

// acts: every method but GET, HEAD and OPTIONS, and every upgrade.
func acts(r *http.Request) bool {
	switch r.Method {
	case http.MethodGet, http.MethodHead, http.MethodOptions:
		return isUpgrade(r)
	}
	return true
}

func isUpgrade(r *http.Request) bool {
	if r.Header.Get("Upgrade") == "" {
		return false
	}
	for _, v := range r.Header.Values("Connection") {
		for _, tok := range strings.Split(v, ",") {
			if strings.EqualFold(strings.TrimSpace(tok), "upgrade") {
				return true
			}
		}
	}
	return false
}

// peer is the client's address as the limiter and `cip` see it: the TCP
// peer, or, when that peer is in trusted_proxies, the right-most
// X-Forwarded-For entry that is not. "" for a unix-socket peer.
func (f *Front) peer(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return ""
	}
	ip, err := netip.ParseAddr(host)
	if err != nil {
		return ""
	}
	ip = ip.Unmap()
	if !f.trusted(ip) {
		return ip.String()
	}
	var hops []string
	for _, v := range r.Header.Values("X-Forwarded-For") {
		hops = append(hops, strings.Split(v, ",")...)
	}
	for i := len(hops) - 1; i >= 0; i-- {
		hop, err := netip.ParseAddr(strings.TrimSpace(hops[i]))
		if err != nil {
			break
		}
		hop = hop.Unmap()
		ip = hop
		if !f.trusted(hop) {
			break
		}
	}
	return ip.String()
}

func (f *Front) trusted(ip netip.Addr) bool {
	for _, p := range f.plan.Trusted {
		if p.Contains(ip) {
			return true
		}
	}
	return false
}

// scheme is what the client used: https under TLS, or behind an upstream
// that terminates it (an https url:).
func (f *Front) scheme(r *http.Request) string {
	if r.TLS != nil || (f.plan.URL != nil && f.plan.URL.Scheme == "https") {
		return "https"
	}
	return "http"
}

// cookieName is `__Host-flyball` when the cookie is Secure, else
// `flyball-<listen port>` (§WP0-4).
func cookieName(p Plan) string {
	if p.Secure {
		return "__Host-flyball"
	}
	if _, port, err := net.SplitHostPort(p.Listen); err == nil {
		return "flyball-" + port
	}
	return "flyball"
}

func (f *Front) setCookie(w http.ResponseWriter, value string, maxAge int) {
	http.SetCookie(w, &http.Cookie{Name: f.cookie, Value: value, Path: "/", MaxAge: maxAge,
		HttpOnly: true, SameSite: http.SameSiteLaxMode, Secure: f.plan.Secure})
}

func (f *Front) clearCookie(w http.ResponseWriter) { f.setCookie(w, "", -1) }

// AuthInfo is GET <root>/api/auth, v2 (§WP0-2).
type AuthInfo struct {
	V         int       `json:"v"`
	Shape     string    `json:"shape"`
	Scheme    string    `json:"scheme"`
	User      *AuthUser `json:"user"`
	Verbs     []string  `json:"verbs"`
	Anonymous string    `json:"anonymous"`
	Login     AuthLogin `json:"login"`
	Exposure  *Exposure `json:"exposure"`
}

// AuthUser is AuthInfo.user.
type AuthUser struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Kind string `json:"kind"`
}

// AuthLogin is AuthInfo.login.
type AuthLogin struct {
	Password bool    `json:"password"`
	Token    bool    `json:"token"`
	Passkey  bool    `json:"passkey"`
	SSO      *string `json:"sso"`
}

// Exposure is the UI's Exposure (wire.ts): where the front serves against
// where it was asked to, with the banner.
type Exposure struct {
	Requested   string  `json:"requested"`
	Host        string  `json:"host"`
	Port        int     `json:"port"`
	Open        bool    `json:"open"`
	Restricted  bool    `json:"restricted"`
	OpenNetwork bool    `json:"open_network"`
	Warning     *string `json:"warning"`
}

// exposure is nil when there is nothing to say: loopback, as asked, with
// no warning.
func (f *Front) exposure() *Exposure {
	p := f.plan
	if p.Fallback == "" && len(p.Warnings) == 0 && loopbackListen(p.Listen) {
		return nil
	}
	host, port, _ := net.SplitHostPort(p.Listen)
	n, _ := strconv.Atoi(port)
	e := &Exposure{Requested: p.Requested, Host: host, Port: n, Open: p.Shape == ShapeLocal,
		Restricted: p.Listen != p.Requested, OpenNetwork: p.Shape == ShapeLocal && !loopbackListen(p.Listen)}
	if b := p.Banner(); b != "" {
		e.Warning = &b
	}
	return e
}

func (f *Front) info(c Caller, rig *Rig) AuthInfo {
	name := grants.AllRigs
	if rig != nil {
		name = rig.Name
	}
	info := AuthInfo{V: 2, Shape: f.plan.Shape, Scheme: c.Scheme, Verbs: f.Verbs(c, name),
		Anonymous: f.plan.Anonymous, Login: AuthLogin{Password: f.plan.Shape == ShapePassword},
		Exposure: f.exposure()}
	if f.plan.Shape == ShapeLocal {
		info.Anonymous = "none"
	}
	if c.Scheme != SchemeAnonymous {
		info.User = &AuthUser{ID: c.Sub, Name: c.Name, Kind: c.Kind}
	}
	return info
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	frontHeaders(w)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	b, _ := json.Marshal(v)
	w.WriteHeader(status)
	w.Write(b)
}

func detail(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"detail": msg})
}

// The fixed strings of the password check (merge requirement 14).
const (
	msgWrongPassword = "Wrong password"
	msgTooMany       = "Too many attempts; try again later"
)

// serveAuth answers <root>/api/auth* (§WP0-8); rig is nil at a daemon root.
func (f *Front) serveAuth(w http.ResponseWriter, r *http.Request, rig *Rig, rel string) {
	switch {
	case rel == "/api/auth":
		f.getInfo(w, r, rig)
	case rel == "/api/auth/login":
		f.login(w, r, rig)
	case rel == "/api/auth/logout":
		f.logout(w, r, rig)
	case rel == "/api/auth/tokens":
		f.tokensRoute(w, r, "")
	case strings.HasPrefix(rel, "/api/auth/tokens/") && !strings.Contains(rel[len("/api/auth/tokens/"):], "/"):
		f.tokensRoute(w, r, rel[len("/api/auth/tokens/"):])
	default: // /api/auth/passkey/* is reserved for Phase 3
		detail(w, http.StatusNotFound, "Not found")
	}
}

func onlyMethods(w http.ResponseWriter, r *http.Request, methods ...string) bool {
	if slices.Contains(methods, r.Method) {
		return true
	}
	w.Header().Set("Allow", strings.Join(methods, ", "))
	detail(w, http.StatusMethodNotAllowed, "Method not allowed")
	return false
}

func (f *Front) refuse(w http.ResponseWriter, r *http.Request, err error) {
	var ae *authError
	if !errors.As(err, &ae) {
		detail(w, http.StatusInternalServerError, "Internal error")
		return
	}
	if ae.clearCookie {
		f.clearCookie(w)
	}
	if ae.status == http.StatusUnauthorized && isUpgrade(r) {
		wsRefuse(w, r, closeSignedOut, ae.detail)
		return
	}
	detail(w, ae.status, ae.detail)
}

func (f *Front) getInfo(w http.ResponseWriter, r *http.Request, rig *Rig) {
	if !onlyMethods(w, r, http.MethodGet, http.MethodHead) {
		return
	}
	c, err := f.authenticate(r, true)
	var stale *staleCookie
	if errors.As(err, &stale) {
		f.clearCookie(w)
	} else if err != nil {
		f.refuse(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, f.info(c, rig))
}

func (f *Front) login(w http.ResponseWriter, r *http.Request, rig *Rig) {
	if !onlyMethods(w, r, http.MethodPost) {
		return
	}
	if f.plan.Shape != ShapePassword {
		detail(w, http.StatusNotFound, "This front has no password sign-in")
		return
	}
	if !f.CheckOrigin(r) {
		detail(w, http.StatusForbidden, "Origin not allowed")
		return
	}
	peer := f.peer(r)
	if ok, wait := f.limiter.Allow(peer); !ok {
		w.Header().Set("Retry-After", strconv.Itoa(int(math.Ceil(wait.Seconds()))))
		detail(w, http.StatusTooManyRequests, msgTooMany)
		return
	}
	var body struct {
		Password string `json:"password"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&body); err != nil {
		detail(w, http.StatusBadRequest, `Send {"password": "..."}`)
		return
	}
	ok, err := f.hasher.Verify(body.Password, f.plan.Password)
	switch {
	case errors.Is(err, store.ErrBusy):
		w.Header().Set("Retry-After", "1")
		detail(w, http.StatusTooManyRequests, msgTooMany)
		return
	case err != nil:
		f.log.Error("front: password check", "err", err)
		detail(w, http.StatusInternalServerError, "Internal error")
		return
	case !ok:
		f.limiter.Failure(peer)
		f.o.Audit.Event("login.fail", slog.String("peer", peer))
		time.Sleep(f.o.FailDelay)
		detail(w, http.StatusUnauthorized, msgWrongPassword)
		return
	}
	cookie, s, err := f.sessions.Create(SubAdmin, grants.All())
	if err != nil {
		detail(w, http.StatusInternalServerError, "Internal error")
		return
	}
	if err := f.o.Audit.Event("login.ok", slog.String("sub", SubAdmin), slog.String("sid", s.Sid), slog.String("peer", peer)); err != nil {
		f.sessions.Delete(s.Sid)
		f.log.Error("front: audit", "err", err)
		detail(w, http.StatusServiceUnavailable, "The audit log cannot be written")
		return
	}
	// A new session at every login; the one the browser held ends.
	if old, err := r.Cookie(f.cookie); err == nil {
		if prev, ok := f.sessions.Lookup(old.Value); ok {
			f.sessions.Delete(prev.Sid)
		}
	}
	f.setCookie(w, cookie, int(store.SessionAbsolute/time.Second))
	writeJSON(w, http.StatusOK, f.info(Caller{Scheme: SchemeSession, Sub: SubAdmin, Name: "admin",
		Kind: store.KindHuman, Sid: s.Sid, Scopes: s.Scopes}, rig))
}

func (f *Front) logout(w http.ResponseWriter, r *http.Request, rig *Rig) {
	if !onlyMethods(w, r, http.MethodPost) {
		return
	}
	if !f.CheckOrigin(r) {
		detail(w, http.StatusForbidden, "Origin not allowed")
		return
	}
	if ck, err := r.Cookie(f.cookie); err == nil {
		if s, ok := f.sessions.Lookup(ck.Value); ok {
			f.sessions.Delete(s.Sid) // OnEnd cancels its sockets and streams
			f.o.Audit.Event("logout", slog.String("sub", s.Subject), slog.String("sid", s.Sid), slog.String("peer", f.peer(r)))
		}
		f.clearCookie(w)
	}
	c := f.anonymous()
	if f.plan.Shape != ShapeLocal {
		c.Scheme = SchemeAnonymous
	}
	writeJSON(w, http.StatusOK, f.info(c, rig))
}

// tokensRoute is GET/POST /api/auth/tokens and DELETE /api/auth/tokens/{id}:
// the admin session or the local shape only.
func (f *Front) tokensRoute(w http.ResponseWriter, r *http.Request, id string) {
	if id == "" && !onlyMethods(w, r, http.MethodGet, http.MethodPost) || id != "" && !onlyMethods(w, r, http.MethodDelete) {
		return
	}
	c, err := f.authenticate(r, false)
	if err != nil {
		f.refuse(w, r, err)
		return
	}
	switch {
	case c.Scheme == SchemeAnonymous:
		detail(w, http.StatusUnauthorized, "Sign in to manage tokens")
		return
	case c.Scheme != SchemeLocal && !(c.Scheme == SchemeSession && c.Sub == SubAdmin):
		detail(w, http.StatusForbidden, "Tokens are managed from the admin session or the local shape")
		return
	}
	if acts(r) && !f.CheckOrigin(r) {
		detail(w, http.StatusForbidden, "Origin not allowed")
		return
	}
	if f.tokens == nil {
		detail(w, http.StatusServiceUnavailable, "Named tokens are unavailable")
		return
	}
	switch {
	case r.Method == http.MethodGet:
		list, err := f.tokens.List()
		if err != nil {
			detail(w, http.StatusServiceUnavailable, "Named tokens are unavailable")
			return
		}
		writeJSON(w, http.StatusOK, list)
	case r.Method == http.MethodPost:
		f.createToken(w, r, c)
	default:
		f.revokeToken(w, r, c, id)
	}
}

func (f *Front) createToken(w http.ResponseWriter, r *http.Request, c Caller) {
	var body struct {
		Name      string   `json:"name"`
		Scopes    []string `json:"scopes"`
		Kind      string   `json:"kind"`
		ExpiresIn *float64 `json:"expires_in"` // seconds; absent: the default
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 16<<10)).Decode(&body); err != nil {
		detail(w, http.StatusBadRequest, `Send {"name", "scopes", "kind", "expires_in"}`)
		return
	}
	refused := func(status int, why string) {
		f.o.Audit.Event("token.create.refused", slog.String("by", c.Sub), slog.String("name", body.Name), slog.String("why", why))
		detail(w, status, why)
	}
	if body.Scopes == nil {
		body.Scopes = []string{grants.Read}
	}
	scopes, err := grants.NormalizeScopes(body.Scopes)
	if err != nil {
		refused(http.StatusBadRequest, err.Error())
		return
	}
	if grants.HasManagement(scopes) {
		refused(http.StatusForbidden, fmt.Sprintf("The %s scope is issued only by `flyball token create`", grants.Management()))
		return
	}
	var life time.Duration
	if body.ExpiresIn != nil {
		if *body.ExpiresIn <= 0 || math.IsInf(*body.ExpiresIn, 0) || *body.ExpiresIn > 1e9 {
			refused(http.StatusBadRequest, "expires_in is a positive number of seconds")
			return
		}
		life = time.Duration(*body.ExpiresIn * float64(time.Second))
	}
	cleartext := f.scheme(r) == "http" && !isLoopbackPeer(r)
	secret, tok, err := f.tokens.Create(store.NewToken{Name: body.Name, Scopes: scopes, Kind: body.Kind,
		Issuer: c.Sub, ExpiresIn: life, Cleartext: cleartext})
	if err != nil {
		var pathErr *fs.PathError
		if errors.As(err, &pathErr) {
			f.log.Error("front: tokens file", "err", err)
			detail(w, http.StatusServiceUnavailable, "Named tokens are unavailable")
			return
		}
		refused(http.StatusBadRequest, err.Error())
		return
	}
	if err := f.o.Audit.Event("token.create", slog.String("by", c.Sub), slog.String("id", tok.ID),
		slog.String("name", tok.Name), slog.Any("scopes", tok.Scopes), slog.String("kind", tok.Kind)); err != nil {
		f.tokens.Revoke(tok.ID)
		detail(w, http.StatusServiceUnavailable, "The audit log cannot be written")
		return
	}
	// {"token", ...row}: the secret, shown once, then the list's row.
	row, _ := json.Marshal(tok)
	secretJSON, _ := json.Marshal(secret)
	writeJSON(w, http.StatusCreated, json.RawMessage(append(append([]byte(`{"token":`), secretJSON...), append([]byte(","), row[1:]...)...)))
}

func (f *Front) revokeToken(w http.ResponseWriter, r *http.Request, c Caller, id string) {
	if err := f.o.Audit.Event("token.revoke", slog.String("by", c.Sub), slog.String("id", id)); err != nil {
		detail(w, http.StatusServiceUnavailable, "The audit log cannot be written")
		return
	}
	switch err := f.tokens.Revoke(id); {
	case errors.Is(err, store.ErrTokenNotFound):
		detail(w, http.StatusNotFound, "No token with that id")
	case err != nil:
		detail(w, http.StatusServiceUnavailable, "Named tokens are unavailable")
	default:
		frontHeaders(w)
		w.WriteHeader(http.StatusNoContent)
	}
}

func isLoopbackPeer(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return true // a unix socket: this machine
	}
	ip, err := netip.ParseAddr(host)
	return err == nil && ip.Unmap().IsLoopback()
}
