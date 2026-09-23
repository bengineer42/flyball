package backend

import (
	"os/exec"
	"testing"
	"time"
)

// A runner that exits 0 on SIGTERM, as flyball-runner does once it
// handles the signal.
const cleanOnTerm = `trap 'exit 0' TERM; while :; do sleep 0.02; done`

// newTestBackend runs script under sh in place of flyball-runner.
func newTestBackend(t *testing.T, script string) *ProcessBackend {
	t.Helper()
	b, err := NewProcessBackend(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	b.command = func(string, []string) *exec.Cmd { return exec.Command("sh", "-c", script) }
	b.minBackoff = 20 * time.Millisecond
	t.Cleanup(func() {
		b.mu.Lock()
		names := make([]string, 0, len(b.runners))
		for name := range b.runners {
			names = append(names, name)
		}
		b.mu.Unlock()
		for _, name := range names {
			b.Stop(name)
		}
	})
	return b
}

func (b *ProcessBackend) pid(name string) int {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok || rp.cmd == nil || rp.cmd.Process == nil {
		return 0
	}
	return rp.cmd.Process.Pid
}

// eventually polls cond for up to 3 s.
func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func mustStart(t *testing.T, b *ProcessBackend, name string) {
	t.Helper()
	if _, err := b.Start(name, "rig.yaml", "127.0.0.1", 1, "", ""); err != nil {
		t.Fatal(err)
	}
}

func TestRestartRestartsARunnerThatExitsCleanlyOnSIGTERM(t *testing.T) {
	b := newTestBackend(t, cleanOnTerm)
	mustStart(t, b, "r")
	first := b.pid("r")
	time.Sleep(100 * time.Millisecond) // let the trap be installed

	if err := b.Restart("r"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "a new process after Restart", func() bool {
		p := b.pid("r")
		return p != 0 && p != first
	})
	if st, _ := b.Status("r"); st == StatusStopped {
		t.Errorf("status after Restart: %s", st)
	}
}
