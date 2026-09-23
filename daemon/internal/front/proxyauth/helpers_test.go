package proxyauth

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/front"
	"flyballd/internal/principal"

	jose "github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

// shortDir is a private temp dir with a path short enough for a unix
// socket (t.TempDir's can pass the 108-byte limit).
func shortDir(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "fb-pa-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	return dir
}

// runner is a fronted runner as far as the front can tell: a real HTTP
// server on a real unix socket that verifies each request's principal and
// echoes it with the headers it received.
type runner struct {
	ep  endpoint.Endpoint
	key principal.Key
	aud string
}

type echo struct {
	Claims  principal.Claims `json:"claims"`
	Headers http.Header      `json:"headers"`
}

func newRunner(t *testing.T) *runner {
	t.Helper()
	rn := &runner{aud: "run-c4c4c4c4", ep: endpoint.Endpoint{Network: "unix", Address: filepath.Join(shortDir(t), "runner.sock")}}
	rand.Read(rn.key[:])
	ln, err := net.Listen("unix", rn.ep.Address)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(rn.serve)}
	go srv.Serve(ln)
	t.Cleanup(func() { srv.Close() })
	return rn
}

func (rn *runner) serve(w http.ResponseWriter, r *http.Request) {
	toks := r.Header.Values(principal.Header)
	if len(toks) != 1 {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	c, err := principal.Verify(toks[0], rn.key, rn.aud, time.Now())
	if err != nil {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	if r.URL.Path == "/api/auth/front" {
		json.NewEncoder(w).Encode(endpoint.FrontInfo{Protocol: 1, Aud: rn.aud, Pid: os.Getpid(), Flyball: "test"})
		return
	}
	json.NewEncoder(w).Encode(echo{Claims: c, Headers: r.Header})
}

// rig is a real front (front.ResolveWith with this package's Factory,
// front.New, front.Listen) in front of a runner, served with ConnContext.
type rig struct {
	t      *testing.T
	plan   front.Plan
	client *http.Client
	base   string
	runner *runner
}

// unixListen is a listen: value on a fresh private socket path.
func unixListen(t *testing.T) string { return "unix:" + filepath.Join(shortDir(t), "front.sock") }

func newRig(t *testing.T, cfg front.Config, o Options) *rig {
	t.Helper()
	if cfg.Auth == "" {
		cfg.Auth = front.ShapeProxy
	}
	if cfg.Anonymous == "" {
		cfg.Anonymous = "read" // so an ignored header shows as anon:, not a 401
	}
	plan, client := front.ResolveWith(cfg, false, Factory(o))
	if plan.Fallback != "" {
		t.Fatalf("shape refused: %s", plan.Fallback)
	}
	rn := newRunner(t)
	f := front.New(front.Options{
		Plan: plan, Proxy: client,
		Route: front.SingleRig(front.Rig{Name: "blender", Target: func(context.Context) (front.Target, error) {
			return front.Target{Endpoint: rn.ep, Aud: rn.aud, Key: rn.key}, nil
		}}),
		Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	ln, err := front.Listen(plan)
	if err != nil {
		t.Fatal(err)
	}
	srv := front.NewServer(plan, f)
	srv.ConnContext = ConnContext
	go srv.Serve(ln)
	t.Cleanup(func() {
		srv.Close()
		f.Close()
		plan.Close()
	})
	rg := &rig{t: t, plan: plan, runner: rn, client: &http.Client{Timeout: 10 * time.Second}}
	if path, ok := strings.CutPrefix(plan.Listen, "unix:"); ok {
		rg.base = "http://localhost"
		rg.client.Transport = &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", path)
		}}
	} else {
		rg.base = "http://" + ln.Addr().String()
	}
	return rg
}

// get sends GET /api/echo with hdr set verbatim (keys as given, so
// non-canonical and underscore spellings go on the wire as written).
func (rg *rig) get(hdr map[string][]string) (int, echo) {
	rg.t.Helper()
	req, err := http.NewRequest("GET", rg.base+"/api/echo", nil)
	if err != nil {
		rg.t.Fatal(err)
	}
	for k, v := range hdr {
		req.Header[k] = v
	}
	resp, err := rg.client.Do(req)
	if err != nil {
		rg.t.Fatal(err)
	}
	defer resp.Body.Close()
	var e echo
	if resp.StatusCode == 200 {
		if err := json.NewDecoder(resp.Body).Decode(&e); err != nil {
			rg.t.Fatal(err)
		}
	}
	return resp.StatusCode, e
}

// mustSub asserts a 200 whose principal is sub.
func (rg *rig) mustSub(hdr map[string][]string, sub string) echo {
	rg.t.Helper()
	code, e := rg.get(hdr)
	if code != 200 {
		rg.t.Fatalf("status %d, want 200 as %s", code, sub)
	}
	if e.Claims.Sub != sub {
		rg.t.Fatalf("sub %q, want %q", e.Claims.Sub, sub)
	}
	return e
}

// mustStatus asserts the status of a request.
func (rg *rig) mustStatus(hdr map[string][]string, want int) {
	rg.t.Helper()
	if code, e := rg.get(hdr); code != want {
		rg.t.Fatalf("status %d (sub %q), want %d", code, e.Claims.Sub, want)
	}
}

// mustAnonymous asserts the headers were ignored: the request went
// through as the anonymous visitor.
func (rg *rig) mustAnonymous(hdr map[string][]string) {
	rg.t.Helper()
	code, e := rg.get(hdr)
	if code != 200 || !strings.HasPrefix(e.Claims.Sub, front.SubAnonymous) {
		rg.t.Fatalf("status %d sub %q, want 200 as anonymous (headers ignored)", code, e.Claims.Sub)
	}
}

// noneOf asserts the runner received none of these headers, in any
// spelling.
func noneOf(t *testing.T, got http.Header, names ...string) {
	t.Helper()
	for k := range got {
		norm := strings.ReplaceAll(strings.ToLower(k), "_", "-")
		for _, n := range names {
			if norm == strings.ToLower(n) {
				t.Errorf("header %q reached the runner", k)
			}
		}
	}
}

func hasScope(e echo, verb string) bool {
	for _, s := range e.Claims.Scp {
		if s == verb {
			return true
		}
	}
	return false
}

// --- a fake identity provider: a real TLS server serving JWKS and OIDC
// discovery, with keys the test controls.

type idp struct {
	t     *testing.T
	srv   *httptest.Server
	mu    sync.Mutex
	keys  []jose.JSONWebKey
	hits  int
	fail  bool
	delay time.Duration // before each JWKS answer
}

var jwksPaths = map[string]bool{
	"/keys": true, "/cdn-cgi/access/certs": true, "/.well-known/pomerium/jwks.json": true,
	"/application/o/flyball/jwks/": true,
}

func newIdP(t *testing.T) *idp {
	t.Helper()
	p := &idp{t: t}
	p.srv = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p.mu.Lock()
		delay := p.delay
		p.mu.Unlock()
		if jwksPaths[r.URL.Path] {
			time.Sleep(delay)
		}
		p.mu.Lock()
		defer p.mu.Unlock()
		switch {
		case r.URL.Path == "/.well-known/openid-configuration":
			if p.fail {
				w.WriteHeader(500)
				return
			}
			json.NewEncoder(w).Encode(map[string]string{"issuer": p.srv.URL, "jwks_uri": p.srv.URL + "/keys"})
		case jwksPaths[r.URL.Path]:
			p.hits++
			if p.fail {
				w.WriteHeader(500)
				return
			}
			json.NewEncoder(w).Encode(jose.JSONWebKeySet{Keys: p.keys})
		default:
			w.WriteHeader(404)
		}
	}))
	t.Cleanup(p.srv.Close)
	return p
}

func (p *idp) add(kid string, priv any, alg jose.SignatureAlgorithm) {
	p.mu.Lock()
	defer p.mu.Unlock()
	var pub any
	switch k := priv.(type) {
	case *rsa.PrivateKey:
		pub = &k.PublicKey
	case *ecdsa.PrivateKey:
		pub = &k.PublicKey
	default:
		pub = priv
	}
	p.keys = append(p.keys, jose.JSONWebKey{Key: pub, KeyID: kid, Algorithm: string(alg), Use: "sig"})
}

func (p *idp) setFail(v bool)           { p.mu.Lock(); p.fail = v; p.mu.Unlock() }
func (p *idp) setDelay(d time.Duration) { p.mu.Lock(); p.delay = d; p.mu.Unlock() }
func (p *idp) jwksHits() int            { p.mu.Lock(); defer p.mu.Unlock(); return p.hits }

// client trusts the IdP's certificate.
func (p *idp) client() *http.Client { return p.srv.Client() }

// anyHostClient reaches the IdP for every host name (for URLs built from
// a vendor host, like <team>.cloudflareaccess.com), verifying its
// certificate as example.com's -- the name httptest's certificate carries.
func (p *idp) anyHostClient() *http.Client {
	base := p.srv.Client().Transport.(*http.Transport)
	addr := p.srv.Listener.Addr().String()
	return &http.Client{Transport: &http.Transport{
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, network, addr)
		},
		TLSClientConfig: &tls.Config{RootCAs: base.TLSClientConfig.RootCAs, ServerName: "example.com"},
	}}
}

var (
	keyOnce sync.Once
	rsaKey  *rsa.PrivateKey
	rsaKey2 *rsa.PrivateKey
	ecKey   *ecdsa.PrivateKey
)

func keys(t *testing.T) {
	keyOnce.Do(func() {
		var err error
		if rsaKey, err = rsa.GenerateKey(rand.Reader, 2048); err != nil {
			t.Fatal(err)
		}
		if rsaKey2, err = rsa.GenerateKey(rand.Reader, 2048); err != nil {
			t.Fatal(err)
		}
		if ecKey, err = ecdsa.GenerateKey(elliptic.P256(), rand.Reader); err != nil {
			t.Fatal(err)
		}
	})
}

// sign makes a compact JWT with a kid header.
func sign(t *testing.T, key any, alg jose.SignatureAlgorithm, kid string, claims map[string]any) string {
	t.Helper()
	opts := (&jose.SignerOptions{}).WithType("JWT")
	if kid != "" {
		opts = opts.WithHeader("kid", kid)
	}
	s, err := jose.NewSigner(jose.SigningKey{Algorithm: alg, Key: key}, opts)
	if err != nil {
		t.Fatal(err)
	}
	tok, err := jwt.Signed(s).Claims(claims).Serialize()
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

// unsigned is an `alg: none` token.
func unsigned(claims map[string]any) string {
	b64 := base64.RawURLEncoding.EncodeToString
	h, _ := json.Marshal(map[string]string{"alg": "none", "typ": "JWT", "kid": "k1"})
	c, _ := json.Marshal(claims)
	return b64(h) + "." + b64(c) + "."
}

// claims is a valid claim set at now.
func claims(iss string, aud any, sub string, now time.Time, extra ...any) map[string]any {
	c := map[string]any{"iss": iss, "aud": aud, "sub": sub, "iat": now.Unix(), "exp": now.Add(5 * time.Minute).Unix()}
	for i := 0; i+1 < len(extra); i += 2 {
		c[extra[i].(string)] = extra[i+1]
	}
	return c
}

// clock is a settable Options.Now.
type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time      { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) add(d time.Duration) { c.mu.Lock(); c.t = c.t.Add(d); c.mu.Unlock() }

func h(kv ...string) map[string][]string {
	out := map[string][]string{}
	for i := 0; i+1 < len(kv); i += 2 {
		out[kv[i]] = append(out[kv[i]], kv[i+1])
	}
	return out
}
