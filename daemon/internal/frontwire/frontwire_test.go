package frontwire

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front"
	"flyballd/internal/fronttest"

	"golang.org/x/crypto/scrypt"
)

func TestDecodeReadsEveryKey(t *testing.T) {
	c, err := Decode(map[string]any{
		"listen": "0.0.0.0:8443", "auth": "password", "url": "https://pi.lab:8443",
		"tls":      map[string]any{"cert": "/c.pem", "key": "/k.pem"},
		"password": "$scrypt$x", "anonymous": "read", "session": "2d",
		"trusted_proxies": []any{"10.0.0.1"},
		"proxy":           map[string]any{"preset": "authelia", "from": "unix", "grants": map[string]any{"all": []any{"alice"}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if c.Listen != "0.0.0.0:8443" || c.Auth != "password" || c.TLS == nil || c.TLS.Cert != "/c.pem" ||
		c.Anonymous != "read" || c.Session != "2d" || len(c.TrustedProxies) != 1 ||
		c.Proxy == nil || c.Proxy.Preset != "authelia" || len(c.Proxy.From) != 1 || c.Proxy.Grants["all"][0] != "alice" {
		t.Fatalf("decoded %+v", c)
	}
}

// Unknown keys and wrong types are errors (the Python model forbids extras).
func TestDecodeRefusesUnknownKeysAndBadTypes(t *testing.T) {
	for name, block := range map[string]map[string]any{
		"unknown": {"listen": "127.0.0.1:8000", "lisen": "x"},
		"type":    {"auth": []any{"local"}},
		"nested":  {"tls": map[string]any{"cert": "/c", "key": "/k", "chain": "/x"}},
	} {
		if _, err := Decode(block); err == nil {
			t.Errorf("%s: no error", name)
		}
	}
}

// A block that cannot be read serves the local shape on a fresh loopback
// address, with the reason in the banner, and refuses (503) the address
// asked for: the block may have asked for password or proxy, and a reverse
// proxy may still forward there (D-028, amended: sec F1).
func TestPlanBadConfigFallsBackToLoopback(t *testing.T) {
	c := front.Config{Listen: "0.0.0.0:18410"}
	p, client := Plan(c, errBad("auth: not a string"), false, ProxyOptions{})
	defer p.Close()
	if client != nil || p.Shape != front.ShapeLocal || p.Listen != "127.0.0.1:0" || p.Requested != "0.0.0.0:18410" ||
		p.Refused != "0.0.0.0:18410" {
		t.Fatalf("plan = %+v", p)
	}
	u := front.Config{Listen: "unix:/run/flyball/front.sock"}
	if q, _ := Plan(u, errBad("lisen: unknown key"), false, ProxyOptions{}); q.Listen != "unix:/run/flyball/front.sock.local" ||
		q.Refused != "unix:/run/flyball/front.sock" || !strings.Contains(q.Banner(), "cannot be read") {
		t.Fatalf("unix plan = %+v", q)
	}
	if !strings.Contains(p.Banner(), "auth: not a string") || !strings.Contains(p.Banner(), "D-028") {
		t.Fatalf("banner = %q", p.Banner())
	}
}

// A block whose YAML type error quotes a secret (yaml.v3 quotes a short
// scalar in full: a secret pasted where a block belongs) keeps it out of
// the refused address's 503: the body is generic (D-028, amended), and the
// reason stays in the banner.
func TestRefusedBodyCarriesNoSecret(t *testing.T) {
	c, bad := Decode(map[string]any{"auth": "proxy", "proxy": "hunter2"})
	if bad == nil || !strings.Contains(bad.Error(), "hunter2") {
		t.Fatalf("the YAML error does not quote the value, so this test shows nothing: %v", bad)
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	c.Listen = ln.Addr().String()
	ln.Close()
	p, _ := Plan(c, bad, false, ProxyOptions{})
	defer p.Close()
	if p.Refused != c.Listen || !strings.Contains(p.Banner(), "hunter2") {
		t.Fatalf("plan %+v", p)
	}
	f := front.New(front.Options{Plan: p})
	defer f.Close()
	var resp *http.Response
	for i := 0; i < 50; i++ {
		if resp, err = http.Get("http://" + c.Listen + "/api/auth"); err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 503 || !strings.Contains(string(b), "authentication is misconfigured") {
		t.Fatalf("refused address: %d %q", resp.StatusCode, b)
	}
	if strings.Contains(string(b), "hunter2") || strings.Contains(string(b), "cannot unmarshal") {
		t.Fatalf("the 503 body carries the reason, and the secret with it: %q", b)
	}
}

// A good block is ResolveWith's plan, with the hook's factory; without
// one, a proxy shape falls back.
func TestPlanUsesThePresetsHook(t *testing.T) {
	old := Presets
	defer func() { Presets = old }()
	Presets = Hooks{}
	c := front.Config{Listen: "127.0.0.1:18411", Auth: "proxy", Proxy: &front.ProxyConfig{Preset: "authelia"}}
	p, _ := Plan(c, nil, false, ProxyOptions{})
	if !strings.Contains(p.Fallback, "no proxy presets") {
		t.Fatalf("with no factory the proxy shape must fall back: %+v", p)
	}
	called := false
	audit := &front.Audit{}
	Presets.Factory = func(o ProxyOptions) front.ProxyFactory {
		if o.Audit != audit {
			t.Error("the factory was not built with the audit")
		}
		return func(*front.ProxyConfig, front.Plan) (front.Client, error) { called = true; return nil, errBad("no") }
	}
	Plan(c, nil, false, ProxyOptions{Audit: audit})
	if !called {
		t.Fatal("Plan did not call the preset factory")
	}
}

// With no HOME (and no XDG_STATE_HOME) there is no private state dir:
// never a shared, predictable one under $TMPDIR.
func TestStateHomeWithoutHomeIsNotTempDir(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", "")
	t.Setenv("HOME", "")
	if got, err := StateHome(); err == nil || strings.HasPrefix(got, os.TempDir()) {
		t.Fatalf("StateHome with no HOME = %q, %v; want an error, not a shared temp path", got, err)
	}
	if got, err := RunDir("abcd1234"); err == nil {
		t.Fatalf("RunDir with no HOME = %q, want an error", got)
	}
	t.Setenv("XDG_STATE_HOME", "relative/state")
	if got, err := StateHome(); err == nil {
		t.Fatalf("StateHome with a relative XDG_STATE_HOME and no HOME = %q, want an error", got)
	}
}

func TestStateDirs(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", "/xdg/state")
	if got, _ := RunDir("abcd1234"); got != "/xdg/state/flyball/front-abcd1234" {
		t.Fatalf("RunDir = %q", got)
	}
	t.Setenv("XDG_STATE_HOME", "")
	t.Setenv("HOME", "/home/u")
	if got, _ := RunDir("abcd1234"); got != "/home/u/.local/state/flyball/front-abcd1234" {
		t.Fatalf("RunDir = %q", got)
	}
	got := DaemonDir("data")
	if !filepath.IsAbs(got) || filepath.Base(got) != "front" {
		t.Fatalf("DaemonDir = %q", got)
	}
}

func TestInsecureOpenEnv(t *testing.T) {
	for v, want := range map[string]bool{"1": true, "yes": true, "TRUE": true, "": false, "0": false} {
		t.Setenv("FLYBALL_INSECURE_OPEN", v)
		if InsecureOpenEnv() != want {
			t.Errorf("FLYBALL_INSECURE_OPEN=%q: %v", v, !want)
		}
	}
}

type errBad string

func (e errBad) Error() string { return string(e) }

// Serve puts Presets.ConnContext on every connection.
func TestServeSetsConnContext(t *testing.T) {
	type key struct{}
	old := Presets
	defer func() { Presets = old }()
	Presets.ConnContext = func(ctx context.Context, c net.Conn) context.Context { return context.WithValue(ctx, key{}, "seen") }
	p := front.Resolve(front.Config{Listen: "127.0.0.1:0"}, false)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	addrs := make(chan net.Addr, 1)
	go func() {
		done <- Serve(ctx, p, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			fmt.Fprint(w, r.Context().Value(key{}))
		}), func(a net.Addr) { addrs <- a })
	}()
	addr := (<-addrs).String()
	var body []byte
	for end := time.Now().Add(5 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		resp, err := http.Get("http://" + addr + "/")
		if err != nil {
			continue
		}
		body, _ = io.ReadAll(resp.Body)
		resp.Body.Close()
		break
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if string(body) != "seen" {
		t.Fatalf("handler saw %q, want the ConnContext value", body)
	}
}

// RunFrontDir is the dir `flyball run` gives its runner, from the rig path.
func TestRunFrontDir(t *testing.T) {
	rt := t.TempDir()
	os.Chmod(rt, 0o700)
	t.Setenv("RUNTIME_DIRECTORY", "")
	t.Setenv("XDG_RUNTIME_DIR", rt)
	dir, ok := RunFrontDir("rig.yaml")
	id, _ := frontdir.FrontID("rig.yaml")
	if !ok || dir != filepath.Join(rt, "flyball", id, "run") {
		t.Fatalf("RunFrontDir = %q, %v", dir, ok)
	}
	t.Setenv("XDG_RUNTIME_DIR", "")
	if _, ok := RunFrontDir("rig.yaml"); ok {
		t.Fatal("no runtime dir: a run's front-dir is a temp dir, not derivable")
	}
}

// The presets are wired in: an authelia front on a unix socket is served
// as asked (no fallback), the proxy's identity reaches the runner's
// principal, and the SO_PEERCRED uid behind it is in the audit (Serve's
// ConnContext).
func TestPresetsAreWired(t *testing.T) {
	dir, err := os.MkdirTemp("", "fw")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	sock := filepath.Join(dir, "front.sock")
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	audit := OpenAudit(dir, logger)
	cfg := front.Config{Listen: "unix:" + sock, Auth: "proxy", Proxy: &front.ProxyConfig{Preset: "authelia", From: front.StringList{"unix"}}}
	plan, proxy := Plan(cfg, nil, false, ProxyOptions{Logger: logger, Audit: audit})
	if plan.Fallback != "" || proxy == nil {
		t.Fatalf("authelia over a unix socket fell back: %q", plan.Fallback)
	}

	var key [32]byte
	rand.Read(key[:])
	runner, err := fronttest.Serve(endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, "r.sock")}, key, "run-x", "")
	if err != nil {
		t.Fatal(err)
	}
	defer runner.Close()
	o := Options(plan, proxy, audit, dir, logger)
	o.Route = front.SingleRig(front.Rig{Name: "r", Target: func(context.Context) (front.Target, error) {
		return front.Target{Endpoint: runner.EP, Aud: runner.Aud, Key: runner.Key}, nil
	}})
	f := front.New(o)
	defer Closer(f, plan, audit)()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	ready := make(chan net.Addr, 1)
	go func() { done <- Serve(ctx, plan, f, func(a net.Addr) { ready <- a }) }()
	<-ready
	defer func() { cancel(); <-done }()

	ep := endpoint.Endpoint{Network: "unix", Address: sock}
	req, _ := http.NewRequest("GET", "http://localhost/api/echo", nil)
	req.Header.Set("Remote-User", "ben")
	resp, err := (&http.Client{Transport: ep.Transport(), Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var e fronttest.Echo
	json.NewDecoder(resp.Body).Decode(&e)
	resp.Body.Close()
	if resp.StatusCode != 200 || e.Claims.Sub != "proxy:authelia#ben" {
		t.Fatalf("%d, principal %+v, want sub proxy:authelia#ben", resp.StatusCode, e.Claims)
	}
	b, _ := os.ReadFile(filepath.Join(dir, AuditFile))
	if !strings.Contains(string(b), `"event":"proxy.peer"`) || !strings.Contains(string(b), `"uid":"`+strconv.Itoa(os.Getuid())+`"`) {
		t.Fatalf("no proxy.peer record with this uid:\n%s", b)
	}
}

// An audit that cannot be opened: the front still serves (D-028), but a
// sign-in, whose record cannot be written, does not happen -- 503 and no
// cookie, as when a single write fails.
func TestUnopenableAuditFailsSignInClosed(t *testing.T) {
	dir := t.TempDir()
	if err := os.Mkdir(filepath.Join(dir, AuditFile), 0o700); err != nil { // a directory where the file goes
		t.Fatal(err)
	}
	audit := OpenAudit(dir, slog.New(slog.NewTextHandler(io.Discard, nil)))
	salt := []byte("0123456789abcdef")
	sum, err := scrypt.Key([]byte("pw"), salt, 16, 1, 1, 64)
	if err != nil {
		t.Fatal(err)
	}
	enc := base64.RawURLEncoding.EncodeToString
	plan := front.Resolve(front.Config{Auth: "password", Password: fmt.Sprintf("$scrypt$n=16,r=1,p=1$%s$%s", enc(salt), enc(sum))}, false)
	if plan.Shape != front.ShapePassword {
		t.Fatalf("plan %+v", plan)
	}
	fo := Options(plan, nil, audit, dir, nil)
	fo.Route = front.SingleRig(front.Rig{Name: "r", Target: func(context.Context) (front.Target, error) {
		return front.Target{}, errors.New("no runner")
	}})
	f := front.New(fo)
	defer Closer(f, plan, audit)()
	srv := httptest.NewServer(f)
	defer srv.Close()
	req, _ := http.NewRequest("POST", srv.URL+"/api/auth/login", strings.NewReader(`{"password":"pw"}`))
	req.Header.Set("Origin", srv.URL)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable || resp.Header.Get("Set-Cookie") != "" {
		t.Fatalf("sign-in with no audit log: %d, Set-Cookie %q; want 503 and none", resp.StatusCode, resp.Header.Get("Set-Cookie"))
	}
}
