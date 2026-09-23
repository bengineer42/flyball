package main

import (
	"bufio"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestRunSurvivesADeadStdoutReader: `flyball run rig.yaml 2>&1 | tee
// out.txt` over SSH, the connection drops and tee dies. The next runner
// line flyball copies to its stdout must not kill it (a Go program
// writing to fd 1 or 2 on a broken pipe dies of SIGPIPE unless SIGPIPE is
// ignored), or the runner is orphaned (D-038). flyball run is this test
// binary re-executed into runDirect, its stdout and stderr one pipe whose
// reader is closed once the runner is talking.
func TestRunSurvivesADeadStdoutReader(t *testing.T) {
	dir := fakeEnv(t)
	t.Setenv("FLYBALL_FAKE_CHATTER", "50ms")
	t.Setenv("FLYBALL_FAKE_LIFETIME", "30s")
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
	cmd := exec.Command(self)
	cmd.Env = append(os.Environ(), "FLYBALL_TEST_RUN_DIRECT="+strings.Join([]string{rig, "--listen", "127.0.0.1:0"}, "\n"))
	cmd.Stdout, cmd.Stderr = pw, pw
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	pw.Close()
	done := make(chan error, 1)
	exited := make(chan struct{})
	go func() { done <- cmd.Wait(); close(exited) }()
	t.Cleanup(func() {
		cmd.Process.Kill() // an error once it has exited: nothing to do
		<-exited
	})

	talking := make(chan string, 1)
	go func() {
		var seen strings.Builder
		sc := bufio.NewScanner(pr)
		for sc.Scan() {
			seen.WriteString(sc.Text() + "\n")
			if strings.Contains(sc.Text(), "fake runner chatter") {
				break
			}
		}
		pr.Close() // the reader dies: every later write gets EPIPE
		talking <- seen.String()
	}()
	select {
	case out := <-talking:
		t.Logf("flyball run's output before its reader died:\n%s", out)
	case <-time.After(15 * time.Second):
		t.Fatal("the runner never printed a line through flyball run")
	}

	// At 50 ms a line, 1.5 s is some thirty writes to the dead pipe.
	select {
	case err := <-done:
		t.Fatalf("flyball run died once its stdout reader had gone: %v (%s); its runner is orphaned", err, cmd.ProcessState)
	case <-time.After(1500 * time.Millisecond):
	}

	// Still a working front: a stop still stops the runner, then flyball.
	if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
		if ws := cmd.ProcessState.Sys().(syscall.WaitStatus); ws.Signaled() {
			t.Fatalf("flyball run was killed by %v after the stop", ws.Signal())
		}
		if code := cmd.ProcessState.ExitCode(); code != 0 {
			t.Fatalf("flyball run exited %d after a clean stop, want 0", code)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("flyball run did not end after SIGTERM")
	}
	if got, _ := os.ReadFile(filepath.Join(dir, "stopped")); string(got) != "terminated" {
		t.Fatalf("the runner saw %q, want the stop forwarded to it (terminated)", got)
	}
}
