package api

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/registry"
)

// fakeBackend records what it was asked to start and never spawns anything.
type fakeBackend struct {
	started []string
	logs    []*logReader
}

// logReader is a log that knows whether it was closed.
type logReader struct {
	io.Reader
	closed bool
}

func (l *logReader) Close() error { l.closed = true; return nil }

func (f *fakeBackend) Start(name string, spec backend.Spec) (string, error) {
	f.started = append(f.started, name)
	return "127.0.0.1:1", nil
}
func (f *fakeBackend) Stop(name string) error    { return nil }
func (f *fakeBackend) Restart(name string) error { return nil }
func (f *fakeBackend) Logs(name string) (io.ReadCloser, error) {
	l := &logReader{Reader: strings.NewReader("log\n")}
	f.logs = append(f.logs, l)
	return l, nil
}
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
	good := config.Manifest{Name: "oven", ServerConfig: "a.yaml", Host: "127.0.0.1", Port: 8101, RootPath: "/oven"}
	if err := reg.Start(good); err != nil {
		t.Fatal(err)
	}
	if err := reg.Start(good); err == nil {
		t.Error("registry started the same name twice")
	}
}

// Each GET .../logs opens the log file; the handler must close it, or
// flyballd runs out of descriptors one request at a time.
func TestLogsClosesTheLog(t *testing.T) {
	s, be := newServer("s3cret")
	if rec := do(t, s, "POST", "/api/runners", "s3cret", goodManifest); rec.Code != http.StatusAccepted {
		t.Fatalf("start: %d", rec.Code)
	}
	for range 3 {
		if rec := do(t, s, "GET", "/api/runners/oven/logs", "s3cret", ""); rec.Code != http.StatusOK {
			t.Fatalf("logs: %d", rec.Code)
		}
	}
	for i, l := range be.logs {
		if !l.closed {
			t.Errorf("log %d left open", i)
		}
	}
}

func TestANonLoopbackHostIs400(t *testing.T) {
	s, be := newServer("s3cret")
	rec := do(t, s, "POST", "/api/runners", "s3cret", `{"name":"oven","server_config":"oven.yaml","port":8101,"host":"0.0.0.0"}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "loopback") {
		t.Errorf("host 0.0.0.0: %d %s, want 400", rec.Code, rec.Body.String())
	}
	if len(be.started) != 0 {
		t.Errorf("started %v", be.started)
	}
}
