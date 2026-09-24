package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front/store"
	"flyballd/internal/fronttest"
	"flyballd/internal/principal"
)

// TestMain lets this test binary stand in for flyball-runner: started as
// `flyball-runner` (a link to it on PATH) with FLYBALLD_FAKE_RUNNER set, it
// serves the endpoint its --front-dir names under its --root-path.
func TestMain(m *testing.M) {
	if os.Getenv("FLYBALLD_FAKE_RUNNER") != "" && filepath.Base(os.Args[0]) == "flyball-runner" {
		os.Exit(fakeRunner())
	}
	if cfg := os.Getenv(daemonEnv); cfg != "" {
		if err := daemonMain(cfg, false); err != nil {
			fmt.Fprintln(os.Stderr, "flyballd:", err)
			os.Exit(1)
		}
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func fakeRunner() int {
	var dir, root string
	for i, a := range os.Args {
		switch {
		case a == "--front-dir" && i+1 < len(os.Args):
			dir = os.Args[i+1]
		case a == "--root-path" && i+1 < len(os.Args):
			root = os.Args[i+1]
		}
	}
	r, err := fronttest.FromFrontDir(dir, root, "fake")
	if err != nil {
		fmt.Fprintln(os.Stderr, "fake runner:", err)
		return 4
	}
	defer r.Close()
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, syscall.SIGINT)
	<-sigs
	return 0
}

// logBuffer collects log output (the banner).
type logBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (l *logBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

func (l *logBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.String()
}

type testDaemon struct {
	t       *testing.T
	addr    string
	dataDir string
	rigDir  string
	logs    *logBuffer
}

// startDaemon runs flyballd (run) on 127.0.0.1:0 with extra flyballd.yaml
// lines and one manifest, oven, for rig; stopped at the test's end, and
// its runners with it (flyballd itself leaves them running, D-037).
func startDaemon(t *testing.T, extra, rig string) *testDaemon {
	t.Helper()
	dir, cfg := daemonFixture(t, extra, rig)
	return serveDaemon(t, dir, cfg, filepath.Join(dir, "data"))
}

// serveDaemon runs flyballd (run) on the flyballd.yaml cfg, the fixture's
// under dir, its data in dataDir; stopped at the test's end, and its
// runners with it.
func serveDaemon(t *testing.T, dir, cfg, dataDir string) *testDaemon {
	t.Helper()
	t.Cleanup(func() { killRunners(t, dir) })

	logs := &logBuffer{}
	log.SetOutput(logs)
	t.Cleanup(func() { log.SetOutput(os.Stderr) })
	ctx, cancel := context.WithCancel(context.Background())
	addrs := make(chan string, 1)
	done := make(chan error, 1)
	go func() { done <- run(ctx, cfg, false, func(a net.Addr) { addrs <- a.String() }) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(20 * time.Second):
			t.Error("flyballd did not stop")
		}
	})
	select {
	case a := <-addrs:
		return &testDaemon{t: t, addr: a, dataDir: dataDir, rigDir: filepath.Join(dir, "rig"), logs: logs}
	case err := <-done:
		t.Fatalf("flyballd: %v\n%s", err, logs)
	case <-time.After(10 * time.Second):
		t.Fatalf("flyballd never listened\n%s", logs)
	}
	return nil
}

// daemonFixture is a flyballd.yaml at dir/flyballd.yaml, its manifests
// and data under dir, one manifest, oven, for rig, and XDG_RUNTIME_DIR at
// dir/rt, so the runners' front-dirs are dir/rt/flyball/<id>/<name>.
func daemonFixture(t *testing.T, extra, rig string) (dir, cfg string) {
	t.Helper()
	return daemonFixtureRigs(t, extra, map[string]string{"oven": rig})
}

// daemonFixtureRigs is daemonFixture with one manifest per rig: name ->
// the rig file's content.
func daemonFixtureRigs(t *testing.T, extra string, rigs map[string]string) (dir, cfg string) {
	t.Helper()
	dir, err := os.MkdirTemp("", "fd")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	os.Mkdir(filepath.Join(dir, "rt"), 0o700)
	t.Setenv("XDG_RUNTIME_DIR", filepath.Join(dir, "rt"))
	t.Setenv("RUNTIME_DIRECTORY", "")
	for _, sub := range []string{"manifests", "rig"} {
		os.Mkdir(filepath.Join(dir, sub), 0o700)
	}
	for name, rig := range rigs {
		rigPath := filepath.Join(dir, "rig", name+".yaml")
		os.WriteFile(rigPath, []byte(rig), 0o600)
		os.WriteFile(filepath.Join(dir, "manifests", name+".yaml"), []byte("name: "+name+"\nserver_config: "+rigPath+"\n"), 0o600)
	}
	cfg = filepath.Join(dir, "flyballd.yaml")
	if !strings.Contains(extra, "listen:") {
		extra = "listen: 127.0.0.1:0\n" + extra
	}
	os.WriteFile(cfg, []byte(fmt.Sprintf("manifests_dir: %s\ndata_dir: %s\n%s", filepath.Join(dir, "manifests"), filepath.Join(dir, "data"), extra)), 0o600)
	return dir, cfg
}

// token makes a named token in flyballd's tokens file, as `flyball token
// create` does.
func (d *testDaemon) token(scopes ...string) string {
	d.t.Helper()
	tokens, err := store.OpenTokens(filepath.Join(d.dataDir, "front", "tokens.json"), store.TokensOptions{})
	if err != nil {
		d.t.Fatal(err)
	}
	defer tokens.Close()
	secret, _, err := tokens.Create(store.NewToken{Name: "t" + fmt.Sprint(time.Now().UnixNano()), Scopes: scopes})
	if err != nil {
		d.t.Fatal(err)
	}
	return secret
}

func (d *testDaemon) get(path, bearer string) (int, []byte) {
	d.t.Helper()
	req, _ := http.NewRequest("GET", "http://"+d.addr+path, nil)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return 0, []byte(err.Error())
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, b
}

// until GETs path until it answers 200 and ok(body), within wait.
func (d *testDaemon) until(path, bearer string, wait time.Duration, ok func([]byte) bool) []byte {
	d.t.Helper()
	var code int
	var body []byte
	for end := time.Now().Add(wait); time.Now().Before(end); time.Sleep(100 * time.Millisecond) {
		code, body = d.get(path, bearer)
		if code == 200 && (ok == nil || ok(body)) {
			return body
		}
	}
	d.t.Fatalf("GET %s: %d %s\n%s", path, code, body, d.logs)
	return nil
}

// fakeOnPath puts this test binary on PATH as flyball-runner.
func fakeOnPath(t *testing.T) {
	t.Helper()
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	bin := t.TempDir()
	if err := os.Symlink(self, filepath.Join(bin, "flyball-runner")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("FLYBALLD_FAKE_RUNNER", "1")
}

// flyballd fronts its runners: each is spawned with a front-dir and reached
// through the front with a principal for its manifest name; management
// needs a bearer token with the management scope; GET /api/auth is the
// front's; a runner killed with -9 is respawned with a fresh key.
func TestDaemonFrontsItsRunners(t *testing.T) {
	fakeOnPath(t)
	d := startDaemon(t, "", "name: oven\n")

	if code, body := d.get("/api/runners", ""); code != 403 {
		t.Fatalf("/api/runners with no token: %d %s, want 403", code, body)
	}
	if code, body := d.get("/api/runners", d.token("read")); code != 403 {
		t.Fatalf("/api/runners with a read token: %d %s, want 403", code, body)
	}
	manage := d.token("manage")
	d.until("/api/runners", manage, 10*time.Second, func(b []byte) bool {
		return strings.Contains(string(b), `"status":"running"`) && strings.Contains(string(b), `"endpoint":"unix:/`)
	})
	var info map[string]any
	json.Unmarshal(d.until("/api/auth", "", 5*time.Second, nil), &info)
	if info["v"] != float64(2) || info["shape"] != "local" {
		t.Fatalf("/api/auth = %v", info)
	}

	var first, second fronttest.Echo
	json.Unmarshal(d.until("/oven/api/echo", "", 10*time.Second, nil), &first)
	if first.Claims.Aud != "oven" || first.Path != "/oven/api/echo" {
		t.Fatalf("echo %+v", first)
	}
	syscall.Kill(first.Pid, syscall.SIGKILL)
	json.Unmarshal(d.until("/oven/api/echo", "", 20*time.Second, func(b []byte) bool {
		var e fronttest.Echo
		return json.Unmarshal(b, &e) == nil && e.Pid != first.Pid
	}), &second)
	if second.Key == first.Key || second.Claims.Aud != "oven" {
		t.Fatalf("respawn: %+v after %+v, want a fresh key, aud oven", second, first)
	}
}

// D-028: a front block that cannot be read (here the old auth: {token})
// serves the local shape on loopback with a banner; the rigs run.
func TestDaemonBadFrontFallsBack(t *testing.T) {
	fakeOnPath(t)
	d := startDaemon(t, "listen: 0.0.0.0:0\nauth:\n  token: s3cret\n  insecure_open: true\n", "name: oven\n")
	if host, _, _ := net.SplitHostPort(d.addr); host != "127.0.0.1" {
		t.Fatalf("listening on %s, want loopback", d.addr)
	}
	d.until("/oven/api/echo", "", 10*time.Second, nil)
	if !strings.Contains(d.logs.String(), "flyball token create") || !strings.Contains(d.logs.String(), "D-028") ||
		strings.Contains(d.logs.String(), "s3cret") {
		t.Fatalf("banner:\n%s", d.logs)
	}
}

// flyballd's state never depends on its cwd: relative manifests_dir and
// data_dir are under flyballd.yaml's directory, whatever directory
// flyballd was started from.
func TestDaemonRelativeDirsUnderItsConfig(t *testing.T) {
	fakeOnPath(t)
	dir, cfg := daemonFixture(t, "", "name: oven\n")
	os.WriteFile(cfg, []byte("listen: 127.0.0.1:0\nmanifests_dir: manifests\ndata_dir: data\n"), 0o600)
	elsewhere := t.TempDir()
	t.Chdir(elsewhere)

	d := serveDaemon(t, dir, cfg, filepath.Join(dir, "data"))
	assertStateAt(t, d, filepath.Join(dir, "data"), elsewhere)
}

// With no manifests_dir or data_dir, flyballd keeps its state in systemd's
// StateDirectory= ($STATE_DIRECTORY), its manifests under it.
func TestDaemonStateDirectory(t *testing.T) {
	fakeOnPath(t)
	dir, cfg := daemonFixture(t, "", "name: oven\n") // manifests in dir/manifests
	os.WriteFile(cfg, []byte("listen: 127.0.0.1:0\n"), 0o600)
	t.Setenv("STATE_DIRECTORY", dir)
	elsewhere := t.TempDir()
	t.Chdir(elsewhere)

	d := serveDaemon(t, dir, cfg, dir)
	assertStateAt(t, d, dir, elsewhere)
}

// assertStateAt: the oven manifest was found and its runner runs, the
// front takes a token from data's tokens file, the runner's log and the
// front's audit are under data, and nothing was made in cwd.
func assertStateAt(t *testing.T, d *testDaemon, data, cwd string) {
	t.Helper()
	d.until("/api/runners", d.token("manage"), 10*time.Second, func(b []byte) bool {
		return strings.Contains(string(b), `"name":"oven"`) && strings.Contains(string(b), `"status":"running"`)
	})
	for _, f := range []string{filepath.Join(data, "logs", "oven.log"), filepath.Join(data, "front", "audit.jsonl")} {
		if _, err := os.Stat(f); err != nil {
			t.Errorf("%v", err)
		}
	}
	if entries, _ := os.ReadDir(cwd); len(entries) != 0 {
		t.Errorf("flyballd wrote into its cwd: %v", entries)
	}
}

// flyballd with the real flyball-runner (examples/simulated/oven.yaml):
// the runner is reachable through the front, a forged principal at its
// socket gets 401, and the management API needs a management token.
func TestDaemonRealRunner(t *testing.T) {
	bin, _ := filepath.Abs("../../../engine/.venv/bin")
	if _, err := os.Stat(filepath.Join(bin, "flyball-runner")); err != nil {
		t.Skipf("no flyball-runner in %s (cd engine && UV_FROZEN=1 uv sync --all-extras)", bin)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	rig, err := os.ReadFile("../../../examples/simulated/oven.yaml")
	if err != nil {
		t.Fatal(err)
	}
	d := startDaemon(t, "", string(rig))
	if code, _ := d.get("/api/runners", ""); code != 403 {
		t.Fatalf("/api/runners with no token: %d, want 403", code)
	}
	manage := d.token("manage")
	d.until("/api/runners", manage, 60*time.Second, func(b []byte) bool { return strings.Contains(string(b), `"status":"running"`) })
	var runner struct {
		Endpoint string `json:"endpoint"`
		RootPath string `json:"root_path"`
	}
	json.Unmarshal(d.until("/oven/api/runner", "", 10*time.Second, nil), &runner)
	if !strings.HasPrefix(runner.Endpoint, "unix:/") || runner.RootPath != "/oven" {
		t.Fatalf("/oven/api/runner = %+v", runner)
	}
	ep, err := endpoint.Parse(runner.Endpoint)
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Dir(ep.Address)
	aud, _ := os.ReadFile(filepath.Join(dir, frontdir.Aud))
	now := time.Now()
	forged, _ := principal.Mint(principal.Key{}, principal.Claims{Sub: "local:console", Sid: "x", Scp: []string{"operate", "read"},
		Kind: "human", Aud: strings.TrimSpace(string(aud)), Sch: "http", Iat: now.Unix(), Exp: now.Add(time.Minute).Unix()})
	req, _ := http.NewRequest("GET", ep.URL("/oven")+"/api/runner", nil)
	req.Header.Set(principal.Header, forged)
	resp, err := (&http.Client{Transport: ep.Transport(), Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 401 {
		t.Fatalf("forged principal at the socket: %d, want 401", resp.StatusCode)
	}
	t.Logf("forged principal at %s: %d %s", ep, resp.StatusCode, resp.Header.Get("X-Flyball-Principal-Error"))
}
