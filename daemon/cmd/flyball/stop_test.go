package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/signal"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/front"
	"flyballd/internal/principal"
)

// --- pidFromLockFile -------------------------------------------------

func TestPidFromLockFileFrontDirShape(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "runner.lock")
	if err := os.WriteFile(path, []byte("pid 4242 rig blender\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	pid, err := pidFromLockFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if pid != 4242 {
		t.Errorf("pid = %d, want 4242", pid)
	}
}

// TestPidFromLockFileBareShape covers the bare runner's own <store>.lock,
// a different tail after the pid (locking.py's hold: "pid <n>: <argv>").
func TestPidFromLockFileBareShape(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "rig.sqlite.lock")
	if err := os.WriteFile(path, []byte("pid 777: flyball-runner rig.yaml\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	pid, err := pidFromLockFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if pid != 777 {
		t.Errorf("pid = %d, want 777", pid)
	}
}

func TestPidFromLockFileBadContent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "runner.lock")
	os.WriteFile(path, []byte("garbage\n"), 0o600)
	if _, err := pidFromLockFile(path); err == nil {
		t.Fatal("expected an error for a lock file with no pid")
	}
}

// --- signalStop --------------------------------------------------------

// TestSignalStopDeliversSIGUSR1 proves stop.go's own responsibility (find
// the pid, send SIGUSR1) works: it signals its own process and listens
// for the signal itself, since the runner-side handler
// (install_break_glass) is Python (A8), out of this package's reach in a
// unit test.
func TestSignalStopDeliversSIGUSR1(t *testing.T) {
	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)
	if err := signalStop(os.Getpid()); err != nil {
		t.Fatal(err)
	}
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("SIGUSR1 was not received")
	}
}

// --- a real front, a fake runner --------------------------------------

type fakeStopRunner struct {
	ep  endpoint.Endpoint
	key principal.Key
	aud string
}

func newFakeStopRunner(t *testing.T, aud string) *fakeStopRunner {
	t.Helper()
	fr := &fakeStopRunner{aud: aud, ep: endpoint.Endpoint{Network: "unix", Address: filepath.Join(t.TempDir(), "sock")}}
	for i := range fr.key {
		fr.key[i] = byte(i + 3)
	}
	ln, err := net.Listen("unix", fr.ep.Address)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(fr.serve)}
	go srv.Serve(ln)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	})
	return fr
}

func (fr *fakeStopRunner) target() front.Target {
	return front.Target{Endpoint: fr.ep, Aud: fr.aud, Key: fr.key}
}

func (fr *fakeStopRunner) serve(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/api/auth/front" {
		if len(r.Header.Values(principal.Header)) == 0 {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"protocol": 1, "aud": fr.aud, "pid": os.Getpid(), "flyball": "test"})
		return
	}
	tok := r.Header.Get(principal.Header)
	if tok == "" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	claims, err := principal.Verify(tok, fr.key, fr.aud, time.Now())
	if err != nil {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	if r.URL.Path != "/api/rig/stop" {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	// The real runner's verb table needs OPERATE for /api/rig/stop
	// (§WP0-7's decided rows); replicate that one check here so
	// TestStopExitsNonZeroOnRefusal actually exercises a refusal instead
	// of a fake that answers 200 unconditionally.
	if !slices.Contains(claims.Scp, "operate") {
		w.WriteHeader(http.StatusForbidden)
		json.NewEncoder(w).Encode(map[string]string{"detail": "needs operate", "needed": "operate"})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"at_ns":  time.Now().UnixNano(),
		"actor":  map[string]string{"sub": claims.Sub, "sid": claims.Sid, "kind": claims.Kind, "via": "http"},
		"reason": "flyball stop", "devices": map[string]any{"pump": map[string]string{"state": "held", "detail": "manual"}},
		"program_interrupted": true, "controllers_manual": []string{"loop"}, "interim": true,
	})
}

func newStopTestFront(t *testing.T, cfg front.Config, fr *fakeStopRunner) *httptest.Server {
	t.Helper()
	plan := front.Resolve(cfg, false)
	opts := front.Options{
		Plan: plan,
		Route: front.SingleRig(front.Rig{Name: "blender", Target: func(context.Context) (front.Target, error) {
			return fr.target(), nil
		}}),
		TokensPath: filepath.Join(t.TempDir(), "front", "tokens.json"),
		FailDelay:  time.Millisecond,
		Sweep:      20 * time.Millisecond,
	}
	f := front.New(opts)
	srv := httptest.NewServer(f)
	t.Cleanup(func() {
		srv.CloseClientConnections()
		f.Close()
		srv.Close()
		plan.Close()
	})
	return srv
}

// TestStopCallsTheRouteWhenTheFrontIsUp: the local shape's anonymous
// caller holds every verb, so no login is needed to prove the happy
// path -- POST /api/rig/stop is reached and its report printed.
func TestStopCallsTheRouteWhenTheFrontIsUp(t *testing.T) {
	fr := newFakeStopRunner(t, "run-test")
	srv := newStopTestFront(t, front.Config{}, fr) // default Auth: "local"
	t.Setenv("FLYBALL_URL", srv.URL)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	out := captureStdout(t, func() {
		if err := runStopCommand("", "", nil); err != nil {
			t.Fatalf("runStopCommand: %v", err)
		}
	})
	if !strings.Contains(out, "stopped:") || !strings.Contains(out, "program interrupted") {
		t.Errorf("stdout = %q, want the printed StopReport", out)
	}
}

// TestStopExitsNonZeroOnRefusal: the password shape, nobody signed in,
// gets refused (401), and that refusal must be reported as an error, not
// silently swallowed or treated as "front unreachable".
func TestStopExitsNonZeroOnRefusal(t *testing.T) {
	fr := newFakeStopRunner(t, "run-test")
	srv := newStopTestFront(t, front.Config{Auth: "password", Password: scryptLineForTest(t)}, fr)
	t.Setenv("FLYBALL_URL", srv.URL)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	err := runStopCommand("", "", nil)
	if err == nil {
		t.Fatal("expected an error for a refused stop")
	}
}

// TestStopFallsBackToFrontDirWhenUnreachable: nothing is listening at
// FLYBALL_URL, so the HTTP attempt fails at the network level, and
// --front-dir's runner.lock supplies the pid to signal instead.
func TestStopFallsBackToFrontDirWhenUnreachable(t *testing.T) {
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1") // nothing listens on port 1
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	dir := t.TempDir()
	lock := filepath.Join(dir, "runner.lock")
	os.WriteFile(lock, []byte("pid "+strconv.Itoa(os.Getpid())+" rig blender\n"), 0o600)

	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	if err := runStopCommand("", "", []string{"--front-dir", dir}); err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("SIGUSR1 was not delivered via the --front-dir fallback")
	}
}

// TestStopWithPidSkipsHTTPEntirely: --pid works even with no front and
// no daemon reachable at all (the bare-runner case).
func TestStopWithPidSkipsHTTPEntirely(t *testing.T) {
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	if err := runStopCommand("", "", []string{"--pid", strconv.Itoa(os.Getpid())}); err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("SIGUSR1 was not delivered via --pid")
	}
}

func TestStopUnreachableWithNoFallbackErrors(t *testing.T) {
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	if err := runStopCommand("", "", nil); err == nil {
		t.Fatal("expected an error when the front is unreachable and no --pid/--front-dir was given")
	}
}

// notifyUSR1 subscribes ch to SIGUSR1 for the life of t, in place of
// this process's default disposition (terminate) -- the tests send
// SIGUSR1 to their own pid to prove stop.go delivers it correctly; the
// runner-side handler (install_break_glass) is Python (A8), out of this
// package's reach here.
func notifyUSR1(t *testing.T, ch chan os.Signal) {
	t.Helper()
	signal.Notify(ch, syscall.SIGUSR1)
	t.Cleanup(func() { signal.Stop(ch) })
}

// scryptLineForTest is a real $scrypt$ line (local.go's own hashPassword),
// so front.Resolve serves the password shape instead of falling back
// (D-028) for a bad or missing line. The password itself doesn't matter
// here: these tests never sign in.
func scryptLineForTest(t *testing.T) string {
	t.Helper()
	line, err := hashPassword("does-not-matter")
	if err != nil {
		t.Fatal(err)
	}
	return line
}

func captureStdout(t *testing.T, fn func()) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	old := os.Stdout
	os.Stdout = w
	fn()
	w.Close()
	os.Stdout = old
	data, _ := io.ReadAll(r)
	return string(data)
}
