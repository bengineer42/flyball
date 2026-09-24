package main

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"flyballd/internal/front"
	"flyballd/internal/frontwire"
)

// --- D-042: a runner named locally is stopped by a signal, never HTTP ---------

// countingServer answers every request with status and counts them: a
// front at FLYBALL_URL that is not the rig being stopped.
func countingServer(t *testing.T, status int, body string) (url string, hits *atomic.Int32) {
	t.Helper()
	hits = new(atomic.Int32)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		w.WriteHeader(status)
		w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv.URL, hits
}

func expectUSR1(t *testing.T, ch chan os.Signal) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("SIGUSR1 was not delivered to the runner named by --front-dir")
	}
}

// TestStopFrontDirNeverStopsAnotherRig: rig A's front answers at
// FLYBALL_URL (the local shape: its stop would succeed), --front-dir names
// rig B. B is signalled; A is not touched at all.
func TestStopFrontDirNeverStopsAnotherRig(t *testing.T) {
	a := newFakeStopRunner(t, "run-a")
	var hits atomic.Int32
	srvA := newStopTestFront(t, front.Config{}, a)
	counted := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		srvA.Config.Handler.ServeHTTP(w, r)
	}))
	t.Cleanup(counted.Close)
	t.Setenv("FLYBALL_URL", counted.URL)
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	dirB := t.TempDir()
	holdRunnerLock(t, dirB, "pid "+strconv.Itoa(os.Getpid())+" rig b\n")
	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	var err error
	out := captureStdout(t, func() { err = runStopCommand("", "", []string{"--front-dir", dirB}) })
	if err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	expectUSR1(t, ch)
	if n := hits.Load(); n != 0 {
		t.Fatalf("rig A's front at FLYBALL_URL got %d request(s); --front-dir must not use HTTP", n)
	}
	if strings.Contains(out, "software stop:") {
		t.Fatalf("a stop report was printed, so rig A was stopped:\n%s", out)
	}
	if !strings.Contains(out, "sent SIGUSR1 to pid") || !strings.Contains(out, "log") {
		t.Errorf("stdout does not say the signal was sent and where the report goes:\n%s", out)
	}
}

// TestStopFrontDirNonexistentFails: a --front-dir with no runner.lock is
// an error, whatever answers at FLYBALL_URL.
func TestStopFrontDirNonexistentFails(t *testing.T) {
	url, hits := countingServer(t, http.StatusOK, `{"at_utc_ns": 1}`)
	t.Setenv("FLYBALL_URL", url)
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	err := runStopCommand("", "", []string{"--front-dir", "/nonexistent"})
	if err == nil {
		t.Fatal("expected an error: /nonexistent has no runner.lock")
	}
	if hits.Load() != 0 {
		t.Fatal("--front-dir made an HTTP call")
	}
	t.Logf("refused: %v", err)
}

// TestStopFrontDirInTheRefusedState: the D-028 refused state -- a front
// whose password line is bad answers 503 on the address it was asked to
// listen on, the address a bare `flyball stop` goes to. --front-dir still
// signals the runner.
func TestStopFrontDirInTheRefusedState(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	requested := ln.Addr().String()
	ln.Close()
	plan := front.Resolve(front.Config{Auth: "password", Password: "hunter2", Listen: requested}, false)
	if plan.Refused != requested {
		t.Fatalf("plan.Refused = %q, want %q (not the refused state)", plan.Refused, requested)
	}
	fr := newFakeStopRunner(t, "run-refused")
	f := front.New(front.Options{
		Plan: plan,
		Route: front.SingleRig(front.Rig{Name: "t", Target: func(context.Context) (front.Target, error) {
			return fr.target(), nil
		}}),
		TokensPath: filepath.Join(t.TempDir(), "front", "tokens.json"),
	})
	t.Cleanup(func() { f.Close(); plan.Close() })
	resp, err := http.Get("http://" + requested + "/api/rig")
	if err != nil {
		t.Fatalf("the refused address does not answer: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("the refused address answers %s, want 503", resp.Status)
	}
	t.Setenv("FLYBALL_URL", "http://"+requested)
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	dir := t.TempDir()
	holdRunnerLock(t, dir, "pid "+strconv.Itoa(os.Getpid())+" rig t\n")
	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	var serr error
	captureStdout(t, func() { serr = runStopCommand("", "", []string{"--front-dir", dir}) })
	if serr != nil {
		t.Fatalf("runStopCommand in the refused state: %v", serr)
	}
	expectUSR1(t, ch)
}

// TestStopFrontDirPastAnUnrelated404: something unrelated answers 404 at
// FLYBALL_URL; --front-dir still signals its runner.
func TestStopFrontDirPastAnUnrelated404(t *testing.T) {
	url, hits := countingServer(t, http.StatusNotFound, "404 page not found")
	t.Setenv("FLYBALL_URL", url)
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	dir := t.TempDir()
	holdRunnerLock(t, dir, "pid "+strconv.Itoa(os.Getpid())+" rig b\n")
	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	var err error
	captureStdout(t, func() { err = runStopCommand("", "", []string{"--front-dir", dir}) })
	if err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	expectUSR1(t, ch)
	if hits.Load() != 0 {
		t.Fatal("--front-dir made an HTTP call")
	}
}

// TestStopRigFileNeverStopsAnotherRig: `flyball stop RIG-FILE` with rig
// A answering at FLYBALL_URL signals the runner of RIG-FILE's front-dir.
func TestStopRigFileNeverStopsAnotherRig(t *testing.T) {
	rt := t.TempDir()
	os.Chmod(rt, 0o700)
	t.Setenv("RUNTIME_DIRECTORY", "")
	t.Setenv("XDG_RUNTIME_DIR", rt)
	url, hits := countingServer(t, http.StatusOK, `{"at_utc_ns": 1, "reason": "stopped the wrong rig"}`)
	t.Setenv("FLYBALL_URL", url)
	t.Setenv("FLYBALLD_URL", url)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	rig := filepath.Join(t.TempDir(), "rig.yaml")
	os.WriteFile(rig, []byte("name: b\n"), 0o600)
	dir, ok := frontwire.RunFrontDir(rig)
	if !ok {
		t.Fatal("RunFrontDir: not derivable in this environment")
	}
	os.MkdirAll(dir, 0o700)
	holdRunnerLock(t, dir, "pid "+strconv.Itoa(os.Getpid())+" rig b\n")
	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	var err error
	out := captureStdout(t, func() { err = runStopCommand("", "", []string{rig}) })
	if err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	expectUSR1(t, ch)
	if hits.Load() != 0 {
		t.Fatal("`flyball stop RIG-FILE` made an HTTP call")
	}
	if !strings.Contains(out, "run.log") {
		t.Errorf("stdout does not name the run.log the report goes to:\n%s", out)
	}
}

// TestStopRigFileArgumentShapes: what counts as a rig file (D-042).
func TestStopRigFileArgumentShapes(t *testing.T) {
	for arg, want := range map[string]bool{
		"rig.yaml": true, "rig.yml": true, "./rig": true, "sub/rig": true, "/abs/x": true,
		"blender": false, "humidity-1": false, "rig.json": false,
	} {
		if got := isRigFileArg(arg); got != want {
			t.Errorf("isRigFileArg(%q) = %v, want %v", arg, got, want)
		}
	}
}

// TestStopServerWithFrontDirIsAUsageError: -s NAME names a rig behind
// flyballd, --front-dir a runner on this host: both at once is refused.
func TestStopServerWithFrontDirIsAUsageError(t *testing.T) {
	url, hits := countingServer(t, http.StatusOK, "")
	t.Setenv("FLYBALL_URL", url)
	t.Setenv("FLYBALLD_URL", url)
	dir := t.TempDir()
	holdRunnerLock(t, dir, "pid "+strconv.Itoa(os.Getpid())+" rig b\n")
	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)
	for _, c := range []struct {
		server string
		args   []string
	}{
		{"blender", []string{"--front-dir", dir}},
		{"", []string{"blender", "--front-dir", dir}},
		{"", []string{"rig.yaml", "--front-dir", dir}},
	} {
		err := runStopCommand(c.server, "", c.args)
		if err == nil || !strings.Contains(err.Error(), "usage") {
			t.Errorf("-s %q stop %v: err = %v, want a usage error", c.server, c.args, err)
		}
	}
	select {
	case <-ch:
		t.Fatal("a usage error still signalled")
	case <-time.After(200 * time.Millisecond):
	}
	if hits.Load() != 0 {
		t.Fatal("a usage error still made an HTTP call")
	}
}
