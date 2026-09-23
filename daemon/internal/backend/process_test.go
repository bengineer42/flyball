package backend

import (
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"testing"
	"time"
)

// A runner that exits 0 on SIGTERM, as flyball-runner does once it
// handles the signal.
const cleanOnTerm = `trap 'exit 0' TERM; while :; do sleep 0.02; done`

// newTestBackend runs script under sh in place of flyball-runner.
func newTestBackend(t *testing.T, script string) *ProcessBackend {
	t.Helper()
	b, err := NewProcessBackend(t.TempDir(), 0)
	if err != nil {
		t.Fatal(err)
	}
	b.command = func(string, []string) *exec.Cmd { return exec.Command("sh", "-c", script) }
	b.minBackoff = 20 * time.Millisecond
	b.ready = func(string, string) bool { return false }
	b.probeInterval = 10 * time.Millisecond
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
	mustStartWith(t, b, name, "")
}

func mustStartWith(t *testing.T, b *ProcessBackend, name, restart string) {
	t.Helper()
	if _, err := b.Start(name, Spec{ServerConfig: "rig.yaml", Host: "127.0.0.1", Port: 1, Restart: restart}); err != nil {
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

// Stop while a crashed runner waits out its backoff must end its life:
// no new process afterwards, and Stop itself succeeds. Run under -race:
// Stop and supervise() both touch the runner's current process.
func TestStopDuringBackoffLeavesNoRunner(t *testing.T) {
	dir := t.TempDir()
	b := newTestBackend(t, "")
	b.minBackoff = 150 * time.Millisecond
	var mu sync.Mutex
	spawned := 0
	b.command = func(string, []string) *exec.Cmd {
		mu.Lock()
		spawned++
		mu.Unlock()
		// The first incarnation crashes; any later one would stay up.
		cmd := exec.Command("sh", "-c", `if [ -e once ]; then sleep 3; else touch once; exit 1; fi`)
		cmd.Dir = dir
		return cmd
	}
	mustStart(t, b, "r")
	eventually(t, "the first crash", func() bool {
		st, _ := b.Status("r")
		return st == StatusRestarting
	})

	if err := b.Stop("r"); err != nil {
		t.Errorf("Stop during backoff: %v", err)
	}
	time.Sleep(4 * b.minBackoff)
	mu.Lock()
	defer mu.Unlock()
	if spawned != 1 {
		t.Errorf("%d processes spawned; a Stop during backoff should leave the one that crashed", spawned)
	}
}

// Stop waits for the runner to go, and kills one that ignores SIGTERM.
func TestStopKillsARunnerThatIgnoresSIGTERM(t *testing.T) {
	b := newTestBackend(t, `trap '' TERM; while :; do sleep 0.02; done`)
	b.stopTimeout = 200 * time.Millisecond
	mustStart(t, b, "r")
	pid := b.pid("r")
	time.Sleep(100 * time.Millisecond) // let the trap be installed

	if err := b.Stop("r"); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
		t.Errorf("runner %d still there after Stop returned (kill -0: %v)", pid, err)
	}
}

func status(b *ProcessBackend, name string) Status {
	st, _ := b.Status(name)
	return st
}

// The status follows the process: starting until the runner answers,
// running, restarting while a crash's backoff runs, then back up.
func TestStatusFollowsACrash(t *testing.T) {
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	b.minBackoff = 300 * time.Millisecond
	var mu sync.Mutex
	answering := false
	b.ready = func(string, string) bool { mu.Lock(); defer mu.Unlock(); return answering }
	mustStart(t, b, "r")

	time.Sleep(50 * time.Millisecond)
	if st := status(b, "r"); st != StatusStarting {
		t.Errorf("before the runner answers: %s, want starting", st)
	}
	mu.Lock()
	answering = true
	mu.Unlock()
	eventually(t, "running", func() bool { return status(b, "r") == StatusRunning })

	first := b.pid("r")
	syscall.Kill(first, syscall.SIGKILL)
	eventually(t, "restarting", func() bool { return status(b, "r") == StatusRestarting })
	eventually(t, "running again, a new process", func() bool {
		return status(b, "r") == StatusRunning && b.pid("r") != first
	})
}

// A runner that exits 0 without being asked is stopped, not running.
func TestACleanExitIsStopped(t *testing.T) {
	b := newTestBackend(t, `exit 0`)
	mustStart(t, b, "r")
	eventually(t, "stopped", func() bool { return status(b, "r") == StatusStopped })
}

// countingCommand runs script and counts how often it was started.
func countingCommand(b *ProcessBackend, script string) func() int {
	var mu sync.Mutex
	n := 0
	b.command = func(string, []string) *exec.Cmd {
		mu.Lock()
		n++
		mu.Unlock()
		return exec.Command("sh", "-c", script)
	}
	return func() int { mu.Lock(); defer mu.Unlock(); return n }
}

func TestRestartNeverLeavesACrashFailed(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `exit 3`)
	mustStartWith(t, b, "r", RestartNever)
	eventually(t, "failed", func() bool { return status(b, "r") == StatusFailed })
	time.Sleep(5 * b.minBackoff)
	if n := spawned(); n != 1 {
		t.Errorf("restart: never started it %d times", n)
	}
}

func TestRestartAlwaysRestartsACleanExit(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `exit 0`)
	mustStartWith(t, b, "r", RestartAlways)
	eventually(t, "a clean exit restarted", func() bool { return spawned() >= 3 })
}

func TestRestartOnFailureStopsAfterACleanExit(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `exit 0`)
	mustStartWith(t, b, "r", RestartOnFailure)
	eventually(t, "stopped", func() bool { return status(b, "r") == StatusStopped })
	time.Sleep(5 * b.minBackoff)
	if n := spawned(); n != 1 {
		t.Errorf("restart: on-failure started a cleanly exited runner %d times", n)
	}
}

// A runner that has stopped or failed comes back on Restart.
func TestRestartBringsBackAFailedRunner(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `sleep 0.1; exit 3`)
	mustStartWith(t, b, "r", RestartNever)
	eventually(t, "failed", func() bool { return status(b, "r") == StatusFailed })
	if err := b.Restart("r"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "started again", func() bool { return spawned() == 2 })
	eventually(t, "failed again", func() bool { return status(b, "r") == StatusFailed })
}

// log_max_size: past the cap the log moves to NAME.log.1 and starts again,
// so a chatty runner cannot fill the disk.
func TestTheCapturedLogIsCapped(t *testing.T) {
	b := newTestBackend(t, `while :; do echo 0123456789012345678901234567890123456789; sleep 0.002; done`)
	b.maxLogSize = 4000
	b.logCheckInterval = 20 * time.Millisecond
	mustStart(t, b, "r")
	log := filepath.Join(b.logDir, "r.log")
	eventually(t, "r.log.1", func() bool {
		_, err := os.Stat(log + ".1")
		return err == nil
	})
	for range 20 {
		time.Sleep(20 * time.Millisecond)
		if fi, err := os.Stat(log); err != nil || fi.Size() > 4*b.maxLogSize {
			t.Fatalf("r.log: %v, %v; the cap is %d", fi.Size(), err, b.maxLogSize)
		}
	}
	if fi, _ := os.Stat(log + ".1"); fi.Size() < b.maxLogSize {
		t.Errorf("r.log.1 is %d bytes, less than the cap it was rotated at", fi.Size())
	}
}

func mode(t *testing.T, path string) os.FileMode {
	t.Helper()
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return fi.Mode().Perm()
}

// Runner logs can hold tokens (a ?token= in an access log line): the
// directory is 0700 and every log file 0600, also ones left 0644 by an
// older flyballd.
func TestLogsAreOwnerOnly(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "logs")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "old.log"), []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	b, err := NewProcessBackend(dir, 0)
	if err != nil {
		t.Fatal(err)
	}
	b.command = func(string, []string) *exec.Cmd {
		return exec.Command("sh", "-c", `while :; do echo 0123456789012345678901234567890123456789; sleep 0.002; done`)
	}
	b.ready = func(string, string) bool { return false }
	b.maxLogSize = 1000
	b.logCheckInterval = 20 * time.Millisecond
	t.Cleanup(func() { b.Stop("new"); b.Stop("old") })

	if m := mode(t, dir); m != 0o700 {
		t.Errorf("log dir %v, want 0700", m)
	}
	for _, name := range []string{"new", "old"} {
		if _, err := b.Start(name, Spec{ServerConfig: "rig.yaml", Host: "127.0.0.1", Port: 1}); err != nil {
			t.Fatal(err)
		}
		if m := mode(t, filepath.Join(dir, name+".log")); m != 0o600 {
			t.Errorf("%s.log %v, want 0600", name, m)
		}
	}
	rotated := filepath.Join(dir, "new.log.1")
	eventually(t, "new.log.1", func() bool { _, err := os.Stat(rotated); return err == nil })
	if m := mode(t, rotated); m != 0o600 {
		t.Errorf("new.log.1 %v, want 0600", m)
	}
}
