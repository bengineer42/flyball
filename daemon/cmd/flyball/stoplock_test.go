package main

import (
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
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

// --- F4: a stop never signals a pid that is not the lock's holder --------

// withoutProcLocks plays a system with no /proc/locks (macOS) for t, and
// sets how it learns a process's start time (nil: it cannot).
func withoutProcLocks(t *testing.T, start func(int) (time.Time, bool)) {
	t.Helper()
	oldLocks, oldStart := procLocks, processStartTime
	procLocks = filepath.Join(t.TempDir(), "no-proc-locks")
	if start == nil {
		start = func(int) (time.Time, bool) { return time.Time{}, false }
	}
	processStartTime = start
	t.Cleanup(func() { procLocks, processStartTime = oldLocks, oldStart })
}

// A stop that lands while a front rewrites the front-dir (frontdir.Write
// holds runner.lock; the file named the exited runner before it) says the
// runner is starting, and signals nothing -- with /proc/locks or without.
func TestStopDuringAFrontWriteSaysStarting(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "run")
	if err := frontdir.Prepare(dir); err != nil {
		t.Fatal(err)
	}
	pid, exited := bystander(t) // the exited runner's pid, since reused
	lock := filepath.Join(dir, frontdir.Lock)
	if err := os.WriteFile(lock, []byte("pid "+strconv.Itoa(pid)+" rig oven\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fifo := filepath.Join(dir, frontdir.Aud+".tmp") // Write blocks opening it: paused, holding the lock
	if err := syscall.Mkfifo(fifo, 0o600); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		_, err := frontdir.Write(dir, "oven", endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, "sock")})
		done <- err
	}()
	t.Cleanup(func() {
		f, err := os.OpenFile(fifo, os.O_RDONLY, 0)
		if err == nil {
			go io.Copy(io.Discard, f)
			<-done
			f.Close()
		}
	})
	for end := time.Now().Add(5 * time.Second); time.Now().Before(end); time.Sleep(10 * time.Millisecond) {
		if held, _ := frontdir.LockHeld(dir); held {
			break
		}
	}
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	err := runStopCommand("", "", []string{"--front-dir", dir})
	t.Logf("with /proc/locks: %v", err)
	if err == nil || !strings.Contains(err.Error(), "starting") {
		t.Errorf("with /proc/locks: %v, want a refusal saying the runner is starting", err)
	}
	withoutProcLocks(t, nil)
	err = runStopCommand("", "", []string{"--front-dir", dir})
	t.Logf("without /proc/locks: %v", err)
	if err == nil || !strings.Contains(err.Error(), "starting") {
		t.Errorf("without /proc/locks: %v, want a refusal saying the runner is starting", err)
	}
	assertAlive(t, exited)
}

// Without /proc/locks, the pid runner.lock names is signalled only when
// that process is known to have been alive when the file was last written
// (it wrote it, and so holds the lock); where that cannot be known, the
// stop refuses and names --pid and Ctrl-C.
func TestStopWithoutProcLocks(t *testing.T) {
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	t.Run("start time unknown", func(t *testing.T) {
		withoutProcLocks(t, nil)
		pid, exited := bystander(t)
		dir := t.TempDir()
		holdRunnerLock(t, dir, "pid "+strconv.Itoa(pid)+" rig blender\n")
		err := runStopCommand("", "", []string{"--front-dir", dir})
		t.Logf("refused: %v", err)
		if err == nil || !strings.Contains(err.Error(), "--pid") || !strings.Contains(err.Error(), "Ctrl-C") {
			t.Errorf("got %v, want a refusal naming --pid and Ctrl-C", err)
		}
		assertAlive(t, exited)
	})
	t.Run("a pid younger than the file", func(t *testing.T) {
		dir := t.TempDir()
		lock := filepath.Join(dir, frontdir.Lock)
		os.WriteFile(lock, nil, 0o600)
		old := time.Now().Add(-time.Hour)
		os.Chtimes(lock, old, old) // written an hour ago, by a runner since gone
		pid, exited := bystander(t)
		withoutProcLocks(t, func(p int) (time.Time, bool) { return time.Now(), p == pid })
		f, err := os.OpenFile(lock, os.O_RDWR, 0)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { f.Close() })
		if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
			t.Fatal(err)
		}
		// The stale content, left as the file's mtime says it was.
		f.WriteString("pid " + strconv.Itoa(pid) + " rig blender\n")
		os.Chtimes(lock, old, old)
		err = runStopCommand("", "", []string{"--front-dir", dir})
		t.Logf("refused: %v", err)
		if err == nil {
			t.Error("signalled a process that started after runner.lock was written")
		}
		assertAlive(t, exited)
	})
	t.Run("the writer", func(t *testing.T) {
		withoutProcLocks(t, func(p int) (time.Time, bool) { return time.Now().Add(-time.Hour), p == os.Getpid() })
		dir := t.TempDir()
		holdRunnerLock(t, dir, "pid "+strconv.Itoa(os.Getpid())+" rig blender\n")
		ch := make(chan os.Signal, 1)
		notifyUSR1(t, ch)
		var err error
		captureStdout(t, func() { err = runStopCommand("", "", []string{"--front-dir", dir}) })
		if err != nil {
			t.Fatalf("runStopCommand: %v", err)
		}
		select {
		case <-ch:
		case <-time.After(2 * time.Second):
			t.Fatal("the runner that wrote runner.lock was not signalled")
		}
	})
}

// --- SIGUSR1 never goes to uv --------------------------------------------

// Under `flyball run --uv` or flyballd's `uv_project:` the process spawned
// is uv, and the runner is its child. uv does not pass SIGUSR1 on: it dies
// of it, orphaning the runner, and the rig is not stopped. `--pid` given
// uv's pid (the pid `flyball runners` shows there) is refused, naming the
// runner under it.
func TestStopPidRefusesUv(t *testing.T) {
	sh, err := exec.LookPath("sh")
	if err != nil {
		t.Skip("no sh")
	}
	uv := filepath.Join(t.TempDir(), "uv") // its comm is "uv"
	if err := os.Symlink(sh, uv); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(uv, "-c", "sleep 30 & wait")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	exited := make(chan struct{})
	go func() { cmd.Wait(); close(exited) }()
	t.Cleanup(func() { syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL); cmd.Process.Kill(); <-exited })
	if b, err := os.ReadFile("/proc/" + strconv.Itoa(cmd.Process.Pid) + "/comm"); err != nil || strings.TrimSpace(string(b)) != "uv" {
		t.Skipf("cannot name a process uv here (%q, %v)", b, err)
	}
	var child int
	for end := time.Now().Add(5 * time.Second); child == 0 && time.Now().Before(end); time.Sleep(10 * time.Millisecond) {
		child = childOf(cmd.Process.Pid)
	}

	err = runStopCommand("", "", []string{"--pid", strconv.Itoa(cmd.Process.Pid)})
	t.Logf("refused: %v", err)
	if err == nil {
		t.Fatal("SIGUSR1 was sent to uv")
	}
	if child != 0 && !strings.Contains(err.Error(), strconv.Itoa(child)) {
		t.Errorf("the refusal does not name the runner under uv (pid %d): %v", child, err)
	}
	assertAlive(t, exited)
}
