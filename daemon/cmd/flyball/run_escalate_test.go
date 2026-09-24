package main

import (
	"bufio"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// --- D-045: repeated Ctrl-C escalates, never orphans the runner ----------

// stubbornRun is `flyball run` (this test binary re-executed into
// runDirect, signals and all) in front of a fake runner that records each
// stop signal and never exits on one.
type stubbornRun struct {
	t       *testing.T
	cmd     *exec.Cmd
	dir     string
	done    chan struct{}
	runner  int
	outDone chan string
}

func startStubbornRun(t *testing.T) *stubbornRun {
	t.Helper()
	dir := fakeEnv(t)
	t.Setenv("FLYBALL_FAKE_STUBBORN", "1")
	rig := filepath.Join(dir, "rig.yaml")
	if err := os.WriteFile(rig, []byte("name: t\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	r := &stubbornRun{t: t, dir: dir, done: make(chan struct{}), outDone: make(chan string, 1)}
	r.cmd = exec.Command(self)
	r.cmd.Env = append(os.Environ(), "FLYBALL_TEST_RUN_DIRECT="+strings.Join([]string{rig, "--listen", "127.0.0.1:0"}, "\n"))
	r.cmd.Stdout, r.cmd.Stderr = pw, pw
	if err := r.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	pw.Close()
	go func() { b, _ := io.ReadAll(bufio.NewReader(pr)); r.outDone <- string(b) }()
	go func() { r.cmd.Wait(); close(r.done) }()
	t.Cleanup(func() {
		r.cmd.Process.Kill()
		<-r.done
		if r.runner > 0 {
			syscall.Kill(-r.runner, syscall.SIGKILL)
		}
	})
	pidFile := filepath.Join(dir, "stopped.pid")
	for end := time.Now().Add(15 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if b, err := os.ReadFile(pidFile); err == nil {
			if n, err := strconv.Atoi(string(b)); err == nil {
				r.runner = n
				// The fake runner is up once it has also subscribed to its
				// signals, which it does before writing its pid.
				return r
			}
		}
	}
	t.Fatal("the fake runner never started")
	return nil
}

func (r *stubbornRun) signal(sig syscall.Signal) {
	r.t.Helper()
	if err := r.cmd.Process.Signal(sig); err != nil {
		r.t.Fatalf("signalling flyball run: %v", err)
	}
}

// seen is the stop signals the runner has recorded, waiting until there are n.
func (r *stubbornRun) seen(n int) []string {
	r.t.Helper()
	var lines []string
	for end := time.Now().Add(5 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if lines = readLines(filepath.Join(r.dir, "stopped")); len(lines) >= n {
			break
		}
	}
	return lines
}

func (r *stubbornRun) exited(within time.Duration) bool {
	select {
	case <-r.done:
		return true
	case <-time.After(within):
		return false
	}
}

func runnerAlive(pid int) bool {
	return syscall.Kill(pid, 0) == nil
}

// TestRunThirdCtrlCKillsAStubbornRunner: a runner that does not exit on
// SIGINT is SIGKILLed on the third Ctrl-C, and flyball exits only after it
// has -- never before, leaving it running.
func TestRunThirdCtrlCKillsAStubbornRunner(t *testing.T) {
	r := startStubbornRun(t)
	r.signal(syscall.SIGINT)
	if got := r.seen(1); len(got) != 1 || got[0] != "interrupt" {
		t.Fatalf("1st Ctrl-C: the runner saw %q, want [interrupt]", got)
	}
	r.signal(syscall.SIGINT)
	if got := r.seen(2); len(got) != 2 || got[1] != "interrupt" {
		t.Fatalf("2nd Ctrl-C: the runner saw %q, want a second interrupt", got)
	}
	if r.exited(500 * time.Millisecond) {
		t.Fatalf("flyball exited after the 2nd Ctrl-C with its runner (pid %d) still running", r.runner)
	}
	if !runnerAlive(r.runner) {
		t.Fatal("the runner died before the 3rd Ctrl-C")
	}
	r.signal(syscall.SIGINT)
	if !r.exited(10 * time.Second) {
		t.Fatal("flyball did not exit after the 3rd Ctrl-C")
	}
	if runnerAlive(r.runner) {
		t.Fatalf("flyball exited and its runner (pid %d) is still running", r.runner)
	}
	if ws := r.cmd.ProcessState.Sys().(syscall.WaitStatus); ws.Signaled() {
		t.Fatalf("flyball was killed by %v, not exiting on its own", ws.Signal())
	}
	out := <-r.outDone
	// A forced kill is not a clean stop: a wrapper must be able to tell.
	if code, want := r.cmd.ProcessState.ExitCode(), 128+int(syscall.SIGKILL); code != want {
		t.Errorf("flyball exited %d after its runner was SIGKILLed, want %d (killed, not stopped):\n%s", code, want, out)
	}
	if !strings.Contains(out, "Ctrl-C again to hurry, a third time kills pid "+strconv.Itoa(r.runner)) {
		t.Errorf("the 1st Ctrl-C did not say what the next ones do:\n%s", out)
	}
}

// TestRunSecondSignalIsSIGINT: the 2nd press sends SIGINT whatever
// arrived (uvicorn force-exits only on a second SIGINT); a SIGTERM to
// flyball is the first press.
func TestRunSecondSignalIsSIGINT(t *testing.T) {
	r := startStubbornRun(t)
	r.signal(syscall.SIGTERM)
	r.seen(1) // two pending SIGTERMs would be one: the kernel merges them
	r.signal(syscall.SIGTERM)
	got := r.seen(2)
	if len(got) != 2 || got[0] != "terminated" || got[1] != "interrupt" {
		t.Fatalf("SIGTERM, SIGTERM: the runner saw %q, want [terminated interrupt]", got)
	}
	if r.exited(300 * time.Millisecond) {
		t.Fatal("flyball exited after two signals with its runner running")
	}
}

// TestRunHangupDoesNotCount: SIGHUP never advances the count (D-038):
// Ctrl-C, two hangups, Ctrl-C is still only the 2nd press.
func TestRunHangupDoesNotCount(t *testing.T) {
	r := startStubbornRun(t)
	r.signal(syscall.SIGINT)
	r.seen(1)
	r.signal(syscall.SIGHUP)
	r.signal(syscall.SIGHUP)
	time.Sleep(200 * time.Millisecond)
	r.signal(syscall.SIGINT)
	r.seen(2)
	if r.exited(500 * time.Millisecond) {
		t.Fatal("flyball exited: a SIGHUP counted as a press")
	}
	if !runnerAlive(r.runner) {
		t.Fatal("the runner was killed: a SIGHUP counted as a press")
	}
	if got := r.seen(2); len(got) != 2 {
		t.Fatalf("the runner saw %q, want two interrupts", got)
	}
	r.signal(syscall.SIGINT)
	if !r.exited(10 * time.Second) {
		t.Fatal("flyball did not exit on the 3rd press")
	}
}
