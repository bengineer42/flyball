package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/registry"
)

// fakeBackend records what it was asked to start and never spawns
// anything; what it reports as the runner's endpoint is `endpoint`.
type fakeBackend struct {
	started  []string
	endpoint string
}

func (f *fakeBackend) Start(name, serverConfig, host string, port int, rootPath, uvProject string) (string, error) {
	f.started = append(f.started, name)
	if f.endpoint != "" {
		return f.endpoint, nil
	}
	return "127.0.0.1:1", nil
}
func (f *fakeBackend) Stop(name string) error                     { return nil }
func (f *fakeBackend) Restart(name string) error                  { return nil }
func (f *fakeBackend) Logs(name string) (io.Reader, error)        { return strings.NewReader("log\n"), nil }
func (f *fakeBackend) Status(name string) (backend.Status, error) { return backend.StatusRunning, nil }

func newServer(token string) (*Server, *fakeBackend) {
	cfg := config.DefaultDaemonConfig()
	cfg.Auth.Token = token
	be := &fakeBackend{}
	return New(registry.New(be), cfg), be
}

func do(t *testing.T, s *Server, method, path, bearer, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, req)
	return rec
}

const goodManifest = `{"name":"oven","server_config":"oven.yaml","port":8101}`

func TestMutatingRoutesAreClosedWithoutAToken(t *testing.T) {
	s, be := newServer("")
	for _, c := range []struct{ method, path string }{
		{"POST", "/api/runners"},
		{"DELETE", "/api/runners/oven"},
		{"POST", "/api/runners/oven/restart"},
		{"GET", "/api/runners/oven/logs"},
	} {
		rec := do(t, s, c.method, c.path, "", goodManifest)
		if rec.Code != http.StatusServiceUnavailable {
			t.Errorf("%s %s with no token configured: got %d, want 503", c.method, c.path, rec.Code)
		}
	}
	if len(be.started) != 0 {
		t.Errorf("a runner was started with no token configured: %v", be.started)
	}
	if rec := do(t, s, "GET", "/api/runners", "", ""); rec.Code != http.StatusOK {
		t.Errorf("GET /api/runners should stay open: got %d", rec.Code)
	}
}

func TestWrongOrMissingBearerIs401(t *testing.T) {
	s, be := newServer("s3cret")
	for _, bearer := range []string{"", "wrong", "s3cret "} {
		rec := do(t, s, "POST", "/api/runners", bearer, goodManifest)
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("bearer %q: got %d, want 401", bearer, rec.Code)
		}
		if got := rec.Header().Get("WWW-Authenticate"); !strings.HasPrefix(got, "Bearer") {
			t.Errorf("bearer %q: WWW-Authenticate %q", bearer, got)
		}
	}
	if len(be.started) != 0 {
		t.Errorf("a runner was started without the token: %v", be.started)
	}
}

func TestRightBearerStartsARunner(t *testing.T) {
	s, be := newServer("s3cret")
	rec := do(t, s, "POST", "/api/runners", "s3cret", goodManifest)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("got %d %s, want 202", rec.Code, rec.Body.String())
	}
	if len(be.started) != 1 || be.started[0] != "oven" {
		t.Errorf("started %v, want [oven]", be.started)
	}
	if rec := do(t, s, "GET", "/api/runners/oven/logs", "s3cret", ""); rec.Code != http.StatusOK || rec.Body.String() != "log\n" {
		t.Errorf("logs: got %d %q", rec.Code, rec.Body.String())
	}
}

func TestAuthReportsTheCallersLevel(t *testing.T) {
	s, _ := newServer("s3cret")
	if rec := do(t, s, "GET", "/api/auth", "", ""); !strings.Contains(rec.Body.String(), `"level":"read"`) {
		t.Errorf("anonymous: %s", rec.Body.String())
	}
	if rec := do(t, s, "GET", "/api/auth", "s3cret", ""); !strings.Contains(rec.Body.String(), `"level":"operate"`) {
		t.Errorf("with the token: %s", rec.Body.String())
	}
}

func TestABadNameOrRootPathIs400NotAStart(t *testing.T) {
	s, be := newServer("s3cret")
	for _, body := range []string{
		`{"name":"../../etc/cron.d/x","server_config":"a.yaml","port":8101}`,
		`{"name":"Oven","server_config":"a.yaml","port":8101}`,
		`{"name":"","server_config":"a.yaml","port":8101}`,
		`{"name":"oven","server_config":"","port":8101}`,
		`{"name":"oven","server_config":"a.yaml","port":0}`,
		`{"name":"oven","server_config":"a.yaml","port":8101,"root_path":"oven"}`,
		`{"name":"oven","server_config":"a.yaml","port":8101,"root_path":"/oven/../x"}`,
		`{"name":"oven","server_config":"a.yaml","port":8101,"root_path":"/"}`,
	} {
		rec := do(t, s, "POST", "/api/runners", "s3cret", body)
		if rec.Code != http.StatusBadRequest {
			t.Errorf("%s: got %d, want 400", body, rec.Code)
		}
	}
	if len(be.started) != 0 {
		t.Errorf("started %v from bad manifests", be.started)
	}
}

func TestLandingPageEscapesTheRootPath(t *testing.T) {
	s, _ := newServer("s3cret")
	rec := do(t, s, "GET", "/", "", "")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Header().Get("Content-Type"), "text/html") {
		t.Fatalf("landing: %d %q", rec.Code, rec.Header().Get("Content-Type"))
	}
	if got := renderLanding([]string{`/a"><b>`}); strings.Contains(got, `"><b>`) || !strings.Contains(got, "&#34;&gt;&lt;b&gt;") {
		t.Errorf("landing page did not escape: %s", got)
	}
}

func TestRegistryRefusesWhatValidateRefuses(t *testing.T) {
	be := &fakeBackend{}
	reg := registry.New(be)
	if err := reg.Start(config.Manifest{Name: "oven", ServerConfig: "a.yaml", Port: 8101, RootPath: `/oven"><script>`}); err == nil {
		t.Error("registry accepted a root path with HTML in it")
	}
	if err := reg.Start(config.Manifest{Name: "../x", ServerConfig: "a.yaml", Port: 8101, RootPath: "/x"}); err == nil {
		t.Error("registry accepted a traversing name")
	}
	if len(be.started) != 0 {
		t.Errorf("backend started %v", be.started)
	}
	good := config.Manifest{Name: "oven", ServerConfig: "a.yaml", Port: 8101, RootPath: "/oven"}
	if err := reg.Start(good); err != nil {
		t.Fatal(err)
	}
	if err := reg.Start(good); err == nil {
		t.Error("registry started the same name twice")
	}
}

// fakeRunner is a runner at /oven with the given door; it counts the
// requests that reach it other than GET /oven/api/auth.
func fakeRunner(t *testing.T, password, token bool) (*httptest.Server, *int) {
	t.Helper()
	proxied := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oven/api/auth" {
			json.NewEncoder(w).Encode(map[string]any{"password": password, "token": token, "level": "operate"})
			return
		}
		proxied++
		io.WriteString(w, "from the runner")
	}))
	t.Cleanup(srv.Close)
	return srv, &proxied
}

func proxyingServer(t *testing.T, listen string, insecureOpen bool, runner *httptest.Server) *Server {
	t.Helper()
	cfg := config.DefaultDaemonConfig()
	cfg.Listen = listen
	cfg.Auth.InsecureOpen = insecureOpen
	be := &fakeBackend{endpoint: strings.TrimPrefix(runner.URL, "http://")}
	reg := registry.New(be)
	if err := reg.Start(config.Manifest{Name: "oven", ServerConfig: "a.yaml", Port: 8101, RootPath: "/oven"}); err != nil {
		t.Fatal(err)
	}
	return New(reg, cfg)
}

// flyballd beyond loopback does not proxy to an open runner: 503, and
// the request never reaches it.
func TestProxyRefusesAnOpenRunnerBeyondLoopback(t *testing.T) {
	runner, proxied := fakeRunner(t, false, false)
	for _, listen := range []string{"0.0.0.0:9000", ":9000", "192.168.1.3:9000"} {
		s := proxyingServer(t, listen, false, runner)
		rec := do(t, s, "POST", "/oven/api/devices/heater/commands/set", "", "{}")
		if rec.Code != http.StatusServiceUnavailable || !strings.Contains(rec.Body.String(), "open") {
			t.Errorf("listen %s: got %d %q, want 503 naming the open runner", listen, rec.Code, rec.Body.String())
		}
	}
	if *proxied != 0 {
		t.Errorf("%d requests reached the open runner", *proxied)
	}
}

func TestProxyServesARunnerWithAPasswordOrToken(t *testing.T) {
	for _, door := range [][2]bool{{true, false}, {false, true}} {
		runner, proxied := fakeRunner(t, door[0], door[1])
		s := proxyingServer(t, "0.0.0.0:9000", false, runner)
		rec := do(t, s, "GET", "/oven/api/devices", "", "")
		if rec.Code != http.StatusOK || rec.Body.String() != "from the runner" || *proxied != 1 {
			t.Errorf("door %v: got %d %q (%d proxied), want the runner's answer", door, rec.Code, rec.Body.String(), *proxied)
		}
	}
}

func TestProxyToAnOpenRunnerOnLoopbackOrOptedIn(t *testing.T) {
	runner, proxied := fakeRunner(t, false, false)
	for _, c := range []struct {
		listen string
		optIn  bool
	}{{"127.0.0.1:9000", false}, {"localhost:9000", false}, {"0.0.0.0:9000", true}} {
		s := proxyingServer(t, c.listen, c.optIn, runner)
		if rec := do(t, s, "GET", "/oven/api/devices", "", ""); rec.Code != http.StatusOK {
			t.Errorf("%+v: got %d %q, want 200", c, rec.Code, rec.Body.String())
		}
	}
	if *proxied != 3 {
		t.Errorf("proxied %d, want 3", *proxied)
	}
}

// A runner whose door cannot be read is treated as open.
func TestProxyRefusesARunnerWhoseDoorCannotBeRead(t *testing.T) {
	broken := httptest.NewServer(http.NotFoundHandler())
	t.Cleanup(broken.Close)
	s := proxyingServer(t, "0.0.0.0:9000", false, broken)
	if rec := do(t, s, "GET", "/oven/api/devices", "", ""); rec.Code != http.StatusServiceUnavailable {
		t.Errorf("got %d %q, want 503", rec.Code, rec.Body.String())
	}
}

func TestStartupWarnings(t *testing.T) {
	cfg := config.DefaultDaemonConfig()
	if w := StartupWarnings(cfg); len(w) != 0 {
		t.Errorf("loopback: %v, want none", w)
	}
	cfg.Listen = "0.0.0.0:9000"
	if w := StartupWarnings(cfg); len(w) != 1 || !strings.Contains(w[0], "unencrypted") {
		t.Errorf("beyond loopback: %v, want the cleartext warning", w)
	}
	cfg.Auth.InsecureOpen = true
	if w := StartupWarnings(cfg); len(w) != 2 || !strings.Contains(w[1], "open") {
		t.Errorf("opted in: %v, want the cleartext and the open warning", w)
	}
}
