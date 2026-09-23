package api

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/endpoint"
	"flyballd/internal/front"
	"flyballd/internal/front/store"
	"flyballd/internal/fronttest"
	"flyballd/internal/registry"

	"golang.org/x/crypto/scrypt"
)

// fakeBackend starts, for each runner, a fronted stand-in (fronttest) on a
// real unix socket with its own key and aud, and hands the front its
// channel. status overrides what Status reports.
type fakeBackend struct {
	t *testing.T

	mu      sync.Mutex
	started []string
	runners map[string]*fronttest.Runner
	status  map[string]backend.Status
	logs    []*logReader
}

// logReader is a log that knows whether it was closed.
type logReader struct {
	io.Reader
	closed bool
}

func (l *logReader) Close() error { l.closed = true; return nil }

func (f *fakeBackend) Start(name string, spec backend.Spec) (string, error) {
	dir, err := os.MkdirTemp("", "api")
	if err != nil {
		return "", err
	}
	f.t.Cleanup(func() { os.RemoveAll(dir) })
	var key [32]byte
	rand.Read(key[:])
	aud := spec.Aud
	if aud == "" {
		aud = name
	}
	r, err := fronttest.Serve(endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, "sock")}, key, aud, spec.RootPath)
	if err != nil {
		return "", err
	}
	f.t.Cleanup(r.Close)
	f.mu.Lock()
	defer f.mu.Unlock()
	f.started = append(f.started, name)
	f.runners[name] = r
	return r.EP.String(), nil
}
func (f *fakeBackend) Stop(name string) error    { return nil }
func (f *fakeBackend) Restart(name string) error { return nil }
func (f *fakeBackend) Logs(name string) (io.ReadCloser, error) {
	l := &logReader{Reader: strings.NewReader("log\n")}
	f.mu.Lock()
	f.logs = append(f.logs, l)
	f.mu.Unlock()
	return l, nil
}
func (f *fakeBackend) Status(name string) (backend.Status, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if st, ok := f.status[name]; ok {
		return st, nil
	}
	return backend.StatusRunning, nil
}
func (f *fakeBackend) Channel(name string) (backend.Channel, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	r, ok := f.runners[name]
	if !ok {
		return backend.Channel{}, fmt.Errorf("no runner %q", name)
	}
	return backend.Channel{Endpoint: r.EP, Aud: r.Aud, Key: r.Key}, nil
}

// daemon is flyballd's front over a registry, served on a real port.
type daemon struct {
	t      *testing.T
	be     *fakeBackend
	reg    *registry.Registry
	url    string
	tokens *store.Tokens
}

const testPassword = "correct horse"

func scryptLine(password string) string {
	salt := []byte("0123456789abcdef")
	sum, err := scrypt.Key([]byte(password), salt, 16, 1, 1, 64)
	if err != nil {
		panic(err)
	}
	enc := base64.RawURLEncoding.EncodeToString
	return fmt.Sprintf("$scrypt$n=16,r=1,p=1$%s$%s", enc(salt), enc(sum))
}

// newDaemon serves flyballd's front in shape (local or password).
func newDaemon(t *testing.T, shape string) *daemon {
	t.Helper()
	be := &fakeBackend{t: t, runners: map[string]*fronttest.Runner{}, status: map[string]backend.Status{}}
	reg := registry.New(be)
	cfg := front.Config{Listen: "127.0.0.1:0", Auth: shape}
	if shape == front.ShapePassword {
		cfg.Password = scryptLine(testPassword)
	}
	plan := front.Resolve(cfg, false)
	if plan.Fallback != "" {
		t.Fatalf("plan fell back: %s", plan.Fallback)
	}
	tokensPath := filepath.Join(t.TempDir(), "tokens.json")
	f := NewFront(reg, front.Options{Plan: plan, TokensPath: tokensPath, FailDelay: 1})
	srv := httptest.NewServer(f)
	t.Cleanup(func() { srv.Close(); f.Close() })
	tokens, err := store.OpenTokens(tokensPath, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	return &daemon{t: t, be: be, reg: reg, url: srv.URL, tokens: tokens}
}

// token makes a named token with scopes, as `flyball token create` does.
func (d *daemon) token(name string, scopes ...string) string {
	d.t.Helper()
	secret, _, err := d.tokens.Create(store.NewToken{Name: name, Scopes: scopes})
	if err != nil {
		d.t.Fatal(err)
	}
	return secret
}

// login signs in with the admin password and returns the session cookie.
func (d *daemon) login() *http.Cookie {
	d.t.Helper()
	req, _ := http.NewRequest("POST", d.url+"/api/auth/login", strings.NewReader(`{"password":"`+testPassword+`"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", d.url)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		d.t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 || len(resp.Cookies()) == 0 {
		d.t.Fatalf("login: %d", resp.StatusCode)
	}
	return resp.Cookies()[0]
}

type cred struct {
	bearer string
	cookie *http.Cookie
}

func (d *daemon) do(method, path string, c cred, body string) (int, string) {
	d.t.Helper()
	req, _ := http.NewRequest(method, d.url+path, strings.NewReader(body))
	req.Header.Set("Origin", d.url)
	if c.bearer != "" {
		req.Header.Set("Authorization", "Bearer "+c.bearer)
	}
	if c.cookie != nil {
		req.AddCookie(c.cookie)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		d.t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(b)
}

func (d *daemon) start(name, root string) {
	d.t.Helper()
	if err := d.reg.Start(config.Manifest{Name: name, ServerConfig: name + ".yaml", Host: "127.0.0.1", RootPath: root}); err != nil {
		d.t.Fatal(err)
	}
}

const goodManifest = `{"name":"oven","server_config":"oven.yaml"}`

// managementRoutes are flyballd's own routes: every one needs a bearer
// token with the management scope.
var managementRoutes = []struct{ method, path, body string }{
	{"GET", "/api/runners", ""},
	{"GET", "/api/runners/kiln", ""},
	{"POST", "/api/runners", goodManifest},
	{"POST", "/api/runners/kiln/restart", ""},
	{"GET", "/api/runners/kiln/logs", ""},
	{"DELETE", "/api/runners/kiln", ""},
	{"GET", "/", ""},
}

// Merge requirement 18 (F21): management needs the management scope by
// bearer. No credential, the local shape's console, an admin session and
// a token without it are all refused.
func TestManagementNeedsTheManagementScopeByBearer(t *testing.T) {
	for _, shape := range []string{front.ShapeLocal, front.ShapePassword} {
		t.Run(shape, func(t *testing.T) {
			d := newDaemon(t, shape)
			manage := d.token("ops", "manage")
			read := d.token("viewer", "read", "operate")
			refused := map[string]struct {
				c    cred
				want int
			}{
				"read token":   {cred{bearer: read}, 403},
				"bad bearer":   {cred{bearer: "fbt1_" + strings.Repeat("A", 43)}, 401},
				"no bearer":    {cred{}, map[string]int{front.ShapeLocal: 403, front.ShapePassword: 401}[shape]},
				"unrecognised": {cred{bearer: "s3cret"}, 401},
			}
			if shape == front.ShapePassword {
				refused["admin session"] = struct {
					c    cred
					want int
				}{cred{cookie: d.login()}, 403}
			}
			for _, r := range managementRoutes {
				d.start("kiln", "/kiln")
				for name, c := range refused {
					if code, body := d.do(r.method, r.path, c.c, r.body); code != c.want {
						t.Errorf("%s %s with %s: %d %s, want %d", r.method, r.path, name, code, body, c.want)
					}
				}
				code, body := d.do(r.method, r.path, cred{bearer: manage}, r.body)
				if code >= 300 {
					t.Errorf("%s %s with the management token: %d %s", r.method, r.path, code, body)
				}
				d.reg.Stop("kiln")
				d.reg.Stop("oven")
			}
		})
	}
}

// GET /api/auth at the daemon root is the front's AuthInfo v2.
func TestAuthAtTheDaemonRoot(t *testing.T) {
	d := newDaemon(t, front.ShapePassword)
	code, body := d.do("GET", "/api/auth", cred{}, "")
	var info front.AuthInfo
	if code != 200 || json.Unmarshal([]byte(body), &info) != nil || info.V != 2 || info.Shape != "password" || info.Scheme != "anonymous" {
		t.Fatalf("/api/auth: %d %s", code, body)
	}
	manage := d.token("ops", "manage")
	code, body = d.do("GET", "/api/auth", cred{bearer: manage}, "")
	if code != 200 || !strings.Contains(body, `"scheme":"token"`) {
		t.Fatalf("/api/auth with a token: %d %s", code, body)
	}
}

// F6 / merge requirement 4: a root path that overlaps another rig's (or
// the daemon's own /api) is refused at registration.
func TestOverlappingRootPathsAreRefused(t *testing.T) {
	d := newDaemon(t, front.ShapeLocal)
	manage := d.token("ops", "manage")
	d.start("lab", "/lab")
	for _, body := range []string{
		`{"name":"inner","server_config":"a.yaml","root_path":"/lab/inner"}`,
		`{"name":"outer","server_config":"a.yaml","root_path":"/lab"}`,
		`{"name":"api","server_config":"a.yaml"}`,
		`{"name":"sub","server_config":"a.yaml","root_path":"/api/sub"}`,
	} {
		if code, resp := d.do("POST", "/api/runners", cred{bearer: manage}, body); code != http.StatusConflict {
			t.Errorf("%s: %d %s, want 409", body, code, resp)
		}
	}
	if code, resp := d.do("POST", "/api/runners", cred{bearer: manage}, `{"name":"lab2","server_config":"a.yaml"}`); code != http.StatusAccepted {
		t.Errorf("/lab2 beside /lab: %d %s, want 202", code, resp)
	}
	if got := d.be.started; len(got) != 2 {
		t.Errorf("started %v, want [lab lab2]", got)
	}
}

// Merge requirement 4: aud and scopes come from the same routed entry --
// the longest root that owns the path.
func TestAudFromRoutedEntry(t *testing.T) {
	d := newDaemon(t, front.ShapePassword)
	d.start("lab", "/lab")
	d.start("lab2", "/lab2")
	tok := d.token("ci", "read:lab2")
	for _, c := range []struct {
		path, aud string
		scp       []string
	}{
		{"/lab/api/echo", "lab", []string{}},
		{"/lab2/api/echo", "lab2", []string{"read"}},
	} {
		code, body := d.do("GET", c.path, cred{bearer: tok}, "")
		var e fronttest.Echo
		if code != 200 || json.Unmarshal([]byte(body), &e) != nil {
			t.Fatalf("%s: %d %s", c.path, code, body)
		}
		if e.Claims.Aud != c.aud || e.Path != c.path || fmt.Sprint(e.Claims.Scp) != fmt.Sprint(c.scp) {
			t.Errorf("%s: aud %q scp %v path %q, want aud %q scp %v", c.path, e.Claims.Aud, e.Claims.Scp, e.Path, c.aud, c.scp)
		}
	}
}

// A runner that is not running is answered by the front, never proxied.
func TestStoppedRunnerIs503(t *testing.T) {
	d := newDaemon(t, front.ShapeLocal)
	d.start("lab", "/lab")
	d.be.mu.Lock()
	d.be.status["lab"] = backend.StatusFailed
	d.be.mu.Unlock()
	if code, body := d.do("GET", "/lab/api/echo", cred{}, ""); code != 503 {
		t.Fatalf("failed runner: %d %s", code, body)
	}
	d.be.mu.Lock()
	d.be.status["lab"] = backend.StatusStarting
	d.be.mu.Unlock()
	if code, body := d.do("GET", "/lab/api/echo", cred{}, ""); code != 503 {
		t.Fatalf("starting runner: %d %s", code, body)
	}
	if code, body := d.do("GET", "/nowhere/api/echo", cred{}, ""); code != 404 {
		t.Fatalf("no rig: %d %s", code, body)
	}
}

func TestABadNameOrRootPathIs400NotAStart(t *testing.T) {
	d := newDaemon(t, front.ShapeLocal)
	manage := d.token("ops", "manage")
	for _, body := range []string{
		`{"name":"../../etc/cron.d/x","server_config":"a.yaml"}`,
		`{"name":"Oven","server_config":"a.yaml"}`,
		`{"name":"","server_config":"a.yaml"}`,
		`{"name":"oven","server_config":""}`,
		`{"name":"oven","server_config":"a.yaml","root_path":"oven"}`,
		`{"name":"oven","server_config":"a.yaml","root_path":"/oven/../x"}`,
		`{"name":"oven","server_config":"a.yaml","root_path":"/"}`,
		`{"name":"oven","server_config":"oven.yaml","host":"0.0.0.0"}`,
	} {
		if code, resp := d.do("POST", "/api/runners", cred{bearer: manage}, body); code != http.StatusBadRequest {
			t.Errorf("%s: got %d %s, want 400", body, code, resp)
		}
	}
	// tcp needs a port where tcp is allowed: Windows only (D-044), taken
	// here by setting GOOS. Elsewhere tcp runs on unix and needs none.
	was := endpoint.GOOS
	endpoint.GOOS = "windows"
	t.Cleanup(func() { endpoint.GOOS = was })
	body := `{"name":"oven","server_config":"a.yaml","network":"tcp","port":0}`
	if code, resp := d.do("POST", "/api/runners", cred{bearer: manage}, body); code != http.StatusBadRequest {
		t.Errorf("windows: %s: got %d %s, want 400", body, code, resp)
	}
	if len(d.be.started) != 0 {
		t.Errorf("started %v from bad manifests", d.be.started)
	}
}

func TestLandingPageEscapesTheRootPath(t *testing.T) {
	d := newDaemon(t, front.ShapeLocal)
	d.start("oven", "/oven")
	code, body := d.do("GET", "/", cred{bearer: d.token("ops", "manage")}, "")
	if code != http.StatusOK || !strings.Contains(body, `href="/oven/"`) {
		t.Fatalf("landing: %d %s", code, body)
	}
	if got := renderLanding([]string{`/a"><b>`}); strings.Contains(got, `"><b>`) || !strings.Contains(got, "&#34;&gt;&lt;b&gt;") {
		t.Errorf("landing page did not escape: %s", got)
	}
}

// Each GET .../logs opens the log file; the handler must close it, or
// flyballd runs out of descriptors one request at a time.
func TestLogsClosesTheLog(t *testing.T) {
	d := newDaemon(t, front.ShapeLocal)
	manage := d.token("ops", "manage")
	if code, body := d.do("POST", "/api/runners", cred{bearer: manage}, goodManifest); code != http.StatusAccepted {
		t.Fatalf("start: %d %s", code, body)
	}
	for range 3 {
		if code, _ := d.do("GET", "/api/runners/oven/logs", cred{bearer: manage}, ""); code != http.StatusOK {
			t.Fatalf("logs: %d", code)
		}
	}
	for i, l := range d.be.logs {
		if !l.closed {
			t.Errorf("log %d left open", i)
		}
	}
}

// GET /api/runners reports each runner's endpoint string.
func TestRunnersReportTheEndpoint(t *testing.T) {
	d := newDaemon(t, front.ShapeLocal)
	d.start("oven", "/oven")
	code, body := d.do("GET", "/api/runners", cred{bearer: d.token("ops", "manage")}, "")
	var out []map[string]any
	if code != 200 || json.Unmarshal([]byte(body), &out) != nil || len(out) != 1 {
		t.Fatalf("%d %s", code, body)
	}
	if ep, _ := out[0]["endpoint"].(string); !strings.HasPrefix(ep, "unix:/") {
		t.Fatalf("endpoint %v", out[0]["endpoint"])
	}
}

// GET /api/rigs is the rigs a caller holds any verb on -- no management
// scope needed -- so `flyball stop --all` can find every rig it may stop.
func TestRigsListsTheRigsTheCallerCanSee(t *testing.T) {
	names := func(body string) string {
		var rigs []struct {
			Name     string `json:"name"`
			RootPath string `json:"root_path"`
			Status   string `json:"status"`
		}
		if err := json.Unmarshal([]byte(body), &rigs); err != nil {
			t.Fatalf("%v: %s", err, body)
		}
		var out []string
		for _, r := range rigs {
			out = append(out, r.Name+"@"+r.RootPath+"="+r.Status)
		}
		return strings.Join(out, ",")
	}
	d := newDaemon(t, front.ShapePassword)
	d.start("oven", "/oven")
	d.start("kiln", "/kiln")
	d.be.status["oven"] = backend.StatusBusy
	for name, c := range map[string]struct {
		c    cred
		want string
	}{
		"operate on kiln only": {cred{bearer: d.token("k", "operate:kiln")}, "kiln@/kiln=running"},
		"read everywhere":      {cred{bearer: d.token("r", "read")}, "kiln@/kiln=running,oven@/oven=busy"},
		"manage only":          {cred{bearer: d.token("m", "manage")}, ""},
		"anonymous (none)":     {cred{}, ""},
	} {
		code, body := d.do("GET", "/api/rigs", c.c, "")
		if code != 200 || names(body) != c.want {
			t.Errorf("%s: %d %s, want 200 %q", name, code, body, c.want)
		}
	}
	if code, _ := d.do("GET", "/api/rigs", cred{bearer: "fbt1_" + strings.Repeat("A", 43)}, ""); code != 401 {
		t.Errorf("a bad bearer: %d, want 401", code)
	}
	local := newDaemon(t, front.ShapeLocal)
	local.start("oven", "/oven")
	if code, body := local.do("GET", "/api/rigs", cred{}, ""); code != 200 || names(body) != "oven@/oven=running" {
		t.Errorf("the local shape's console: %d %s", code, body)
	}
}
