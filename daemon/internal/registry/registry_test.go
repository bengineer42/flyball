package registry

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/endpoint"
)

// fakeBackend's status is whatever the test sets, as a real process's
// would be after a crash.
type fakeBackend struct {
	mu     sync.Mutex
	status backend.Status
}

func (f *fakeBackend) Start(name string, spec backend.Spec) (string, error) {
	return "127.0.0.1:1", nil
}
func (f *fakeBackend) Stop(name string) error    { return nil }
func (f *fakeBackend) Restart(name string) error { return nil }
func (f *fakeBackend) Logs(name string) (io.ReadCloser, error) {
	return io.NopCloser(strings.NewReader("")), nil
}
func (f *fakeBackend) Status(name string) (backend.Status, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.status, nil
}
func (f *fakeBackend) set(s backend.Status) {
	f.mu.Lock()
	f.status = s
	f.mu.Unlock()
}

func TestStatusFollowsTheProcess(t *testing.T) {
	be := &fakeBackend{status: backend.StatusStarting}
	r := New(be)
	m := config.Manifest{Name: "oven", ServerConfig: "oven.yaml", Host: "127.0.0.1", Port: 8101, RootPath: "/oven"}
	if err := r.Start(m); err != nil {
		t.Fatal(err)
	}
	for _, want := range []backend.Status{
		backend.StatusRunning, backend.StatusRestarting, backend.StatusFailed, backend.StatusStopped,
	} {
		be.set(want)
		e, ok := r.Get("oven")
		if !ok {
			t.Fatal("oven not registered")
		}
		if e.Status != want {
			t.Errorf("Get: status %q, the process is %q", e.Status, want)
		}
		if l := r.List(); len(l) != 1 || l[0].Status != want {
			t.Errorf("List: %+v, the process is %q", l, want)
		}
	}
}

// recordingBackend keeps the Spec it was started with and is Fronted.
type recordingBackend struct {
	fakeBackend
	spec backend.Spec
}

func (f *recordingBackend) Start(name string, spec backend.Spec) (string, error) {
	f.spec = spec
	return "unix:/run/flyball/" + name + "/sock", nil
}
func (f *recordingBackend) Channel(name string) (backend.Channel, error) {
	e, _ := endpoint.Parse("unix:/run/flyball/" + name + "/sock")
	return backend.Channel{Endpoint: e, Dir: "/run/flyball/" + name, Aud: name, Key: [32]byte{7}}, nil
}

// The manifest's name is the runner's aud, and its network reaches the
// backend resolved.
func TestStartPassesAudAndNetwork(t *testing.T) {
	be := &recordingBackend{}
	r := New(be)
	m := config.Manifest{Name: "oven", ServerConfig: "oven.yaml", Host: "127.0.0.1", RootPath: "/oven", UvProject: "/srv/oven"}
	if err := r.Start(m); err != nil {
		t.Fatal(err)
	}
	s := be.spec
	if s.Aud != "oven" || s.Network != m.ResolvedNetwork() || s.RootPath != "/oven" || s.ServerConfig != "oven.yaml" || s.UvProject != "/srv/oven" {
		t.Errorf("spec %+v", s)
	}
	e, _ := r.Get("oven")
	if e.Endpoint != "unix:/run/flyball/oven/sock" {
		t.Errorf("entry endpoint %q", e.Endpoint)
	}
	ch, err := r.Channel("oven")
	if err != nil || ch.Aud != "oven" || ch.Key != ([32]byte{7}) {
		t.Errorf("Channel: %+v, %v", ch, err)
	}
	if _, err := r.Channel("kiln"); err == nil {
		t.Error("Channel of an unregistered runner")
	}

	tcp := config.Manifest{Name: "kiln", ServerConfig: "k.yaml", Host: "127.0.0.1", Network: "tcp", Port: 8102, RootPath: "/kiln"}
	if err := r.Start(tcp); err != nil {
		t.Fatal(err)
	}
	if be.spec.Network != "tcp" || be.spec.Port != 8102 || be.spec.Host != "127.0.0.1" {
		t.Errorf("tcp spec %+v", be.spec)
	}
}

// A backend that cannot hand out channels says so.
func TestChannelNeedsAFrontedBackend(t *testing.T) {
	r := New(&fakeBackend{status: backend.StatusRunning})
	if err := r.Start(config.Manifest{Name: "oven", ServerConfig: "oven.yaml", Host: "127.0.0.1", RootPath: "/oven"}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Channel("oven"); err == nil {
		t.Error("Channel from a backend with none")
	}
}

// --- a real runner on a unix socket: this test binary, re-executed ---

const helperEnv = "FLYBALLD_TEST_FAKE_RUNNER"

func TestMain(m *testing.M) {
	if os.Getenv(helperEnv) == "1" {
		fakeRunner(os.Args[1:])
		return
	}
	os.Exit(m.Run())
}

func sign(r *http.Request, key [32]byte, aud string) error {
	r.Header.Set("X-Flyball-Principal", fmt.Sprintf("%x/%s", key, aud))
	return nil
}

func fakeRunner(args []string) {
	var dir, root string
	for i := 0; i+1 < len(args); i++ {
		switch args[i] {
		case "--front-dir":
			dir = args[i+1]
		case "--root-path":
			root = args[i+1]
		}
	}
	key, err1 := os.ReadFile(filepath.Join(dir, "key"))
	aud, err2 := os.ReadFile(filepath.Join(dir, "aud"))
	ep, err3 := os.ReadFile(filepath.Join(dir, "endpoint"))
	if dir == "" || err1 != nil || err2 != nil || err3 != nil {
		os.Exit(4)
	}
	e, err := endpoint.Parse(strings.TrimSpace(string(ep)))
	if err != nil {
		os.Exit(4)
	}
	want := strings.TrimSpace(string(key)) + "/" + strings.TrimSpace(string(aud))
	l, err := net.Listen(e.Network, e.Address)
	if err != nil {
		os.Exit(1)
	}
	http.Serve(l, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != root+"/api/auth/front" {
			http.NotFound(w, r)
			return
		}
		if r.Header.Get("X-Flyball-Principal") != want {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		fmt.Fprintf(w, `{"protocol":1,"aud":%q,"pid":%d,"flyball_version":"test"}`, strings.TrimSpace(string(aud)), os.Getpid())
	}))
}

// The registry shows a unix-socket runner as running once it passes the
// handshake, through a real ProcessBackend and a real process.
func TestUnixRunnerShowsRunning(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("no unix sockets for the runner on Windows")
	}
	be, err := backend.NewProcessBackend(t.TempDir(), 0)
	if err != nil {
		t.Fatal(err)
	}
	be.SetFront(backend.FrontOptions{Sign: sign})
	// flyball-runner is this test binary; uv_project is not used.
	bin := t.TempDir()
	script := filepath.Join(bin, "flyball-runner")
	body := fmt.Sprintf("#!/bin/sh\n%s=1 exec %q \"$@\"\n", helperEnv, os.Args[0])
	if err := os.WriteFile(script, []byte(body), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))

	r := New(be)
	t.Cleanup(func() { r.Stop("oven") })
	if err := r.Start(config.Manifest{Name: "oven", ServerConfig: "oven.yaml", Host: "127.0.0.1", RootPath: "/oven"}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		e, _ := r.Get("oven")
		if e.Status == backend.StatusRunning {
			if !strings.HasPrefix(e.Endpoint, "unix:/") {
				t.Errorf("endpoint %q", e.Endpoint)
			}
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("status %s after 5 s, endpoint %s", e.Status, e.Endpoint)
		}
		time.Sleep(20 * time.Millisecond)
	}
}
