package registry

import (
	"io"
	"strings"
	"sync"
	"testing"

	"flyballd/internal/backend"
	"flyballd/internal/config"
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
