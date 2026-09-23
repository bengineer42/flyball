package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"flyballd/internal/frontwire"
)

// --- a stale or foreign runner.lock is never signalled ----------------------

// bystander is a live process that is not a runner and would die of
// SIGUSR1 (a plain sleep, default disposition): the pid a stale
// runner.lock names once the kernel has reused it. exited is closed when
// it ends.
func bystander(t *testing.T) (pid int, exited chan struct{}) {
	t.Helper()
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	exited = make(chan struct{})
	go func() { cmd.Wait(); close(exited) }()
	t.Cleanup(func() { cmd.Process.Kill(); <-exited })
	return cmd.Process.Pid, exited
}

func assertAlive(t *testing.T, exited chan struct{}) {
	t.Helper()
	select {
	case <-exited:
		t.Fatal("the bystander named by runner.lock was killed (SIGUSR1 went to a process that is not the runner)")
	case <-time.After(500 * time.Millisecond):
	}
}

// TestStopRefusesAStaleRunnerLock: runner.lock names a live process but
// nothing holds the lock (its runner died; runner.lock is never
// unlinked) -- the pid is not signalled.
func TestStopRefusesAStaleRunnerLock(t *testing.T) {
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	pid, exited := bystander(t)
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "runner.lock"), []byte("pid "+strconv.Itoa(pid)+" rig blender\n"), 0o600)

	err := runStopCommand("", "", []string{"--front-dir", dir})
	if err == nil {
		t.Error("expected an error: no runner holds runner.lock")
	} else {
		t.Logf("refused: %v", err)
	}
	assertAlive(t, exited)
}

// TestStopRefusesAPidThatDoesNotHoldTheLock: runner.lock is held, but not
// by the pid it names -- the named process is not the runner.
func TestStopRefusesAPidThatDoesNotHoldTheLock(t *testing.T) {
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	pid, exited := bystander(t)
	dir := t.TempDir()
	holdRunnerLock(t, dir, "pid "+strconv.Itoa(pid)+" rig blender\n")

	err := runStopCommand("", "", []string{"--front-dir", dir})
	if err == nil {
		t.Error("expected an error: the pid runner.lock names does not hold it")
	} else {
		t.Logf("refused: %v", err)
	}
	assertAlive(t, exited)
}

// TestStopRigFileRefusesAStaleRunnerLock: the same for `flyball stop
// RIG-FILE`'s derived front-dir.
func TestStopRigFileRefusesAStaleRunnerLock(t *testing.T) {
	rt := t.TempDir()
	os.Chmod(rt, 0o700)
	t.Setenv("RUNTIME_DIRECTORY", "")
	t.Setenv("XDG_RUNTIME_DIR", rt)
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("FLYBALLD_URL", "http://127.0.0.1:1")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	rig := filepath.Join(t.TempDir(), "rig.yaml")
	os.WriteFile(rig, []byte("name: t\n"), 0o600)
	dir, ok := frontwire.RunFrontDir(rig)
	if !ok {
		t.Fatal("RunFrontDir: not derivable in this environment")
	}
	os.MkdirAll(dir, 0o700)
	pid, exited := bystander(t)
	os.WriteFile(filepath.Join(dir, "runner.lock"), []byte("pid "+strconv.Itoa(pid)+" rig blender\n"), 0o600)

	if err := runStopCommand("", "", []string{rig}); err == nil {
		t.Error("expected an error: no runner holds the rig file's runner.lock")
	} else {
		t.Logf("refused: %v", err)
	}
	assertAlive(t, exited)
}
