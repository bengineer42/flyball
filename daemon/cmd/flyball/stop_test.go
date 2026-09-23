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
	"flyballd/internal/frontwire"
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
		"reason": "flyball stop", "devices": map[string]any{"pump": map[string]string{"state": "unchanged", "detail": "manual"}},
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
	if !strings.Contains(out, "software stop:") || !strings.Contains(out, "program interrupted") {
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

// TestStopDerivesFrontDirFromARigFilePath: `flyball stop RIG-FILE` (no
// --front-dir, no --pid), the front unreachable, falls back to the same
// front-dir `flyball run RIG-FILE` would have used
// (frontwire.RunFrontDir), so a rig started with `flyball run rig.yaml`
// can be stopped with `flyball stop rig.yaml` from elsewhere.
func TestStopDerivesFrontDirFromARigFilePath(t *testing.T) {
	rt := t.TempDir()
	os.Chmod(rt, 0o700)
	t.Setenv("RUNTIME_DIRECTORY", "")
	t.Setenv("XDG_RUNTIME_DIR", rt)
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1") // nothing listens on port 1
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	rigDir := t.TempDir()
	rig := filepath.Join(rigDir, "rig.yaml")
	if err := os.WriteFile(rig, []byte("name: t\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	dir, ok := frontwire.RunFrontDir(rig)
	if !ok {
		t.Fatal("RunFrontDir: not derivable in this environment")
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	lock := filepath.Join(dir, "runner.lock")
	if err := os.WriteFile(lock, []byte("pid "+strconv.Itoa(os.Getpid())+" rig blender\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	if err := runStopCommand("", "", []string{rig}); err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("SIGUSR1 was not delivered via the rig-path front-dir fallback")
	}
}

// TestStopWithNonRigNameStillErrors: a NAME that isn't a real file on
// disk (the ordinary daemon-registered-runner-name case) must not be
// treated as a rig path -- no lock file exists to guess, so the usual
// "pass --front-dir or --pid" error still applies.
func TestStopWithNonRigNameStillErrors(t *testing.T) {
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("FLYBALLD_URL", "http://127.0.0.1:1")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	if err := runStopCommand("", "", []string{"not-a-real-rig-file"}); err == nil {
		t.Fatal("expected an error for a name that is not a rig file on disk")
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

// --- a stop is never blockable ------------------------------------------

// hungListener accepts connections and never answers them: a front that
// is up but wedged. The accepted connections are left open until the test
// binary exits, so a client with no deadline stays blocked on them.
func hungListener(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		var held []net.Conn
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			held = append(held, c)
		}
	}()
	return "http://" + ln.Addr().String()
}

// holdRunnerLock writes content to dir/runner.lock and holds LOCK_EX on it
// for the life of t, as a live runner holds its own (locking.py's
// hold_front) -- from this process, so the holder's pid is os.Getpid().
func holdRunnerLock(t *testing.T, dir, content string) {
	t.Helper()
	f, err := os.OpenFile(filepath.Join(dir, "runner.lock"), os.O_CREATE|os.O_RDWR|os.O_TRUNC, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(content); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.Close() })
}

// shortStopTimeout makes stopTimeout 500 ms for t, for the tests that
// exercise the bound rather than its value.
func shortStopTimeout(t *testing.T) {
	old := stopTimeout
	stopTimeout = 500 * time.Millisecond
	t.Cleanup(func() { stopTimeout = old })
}

// stopWithin runs `flyball stop args...` and fails the test if it has not
// returned within bound.
func stopWithin(t *testing.T, bound time.Duration, args ...string) error {
	t.Helper()
	done := make(chan error, 1)
	go func() { done <- runStopCommand("", "", args) }()
	select {
	case err := <-done:
		return err
	case <-time.After(bound):
		t.Fatalf("flyball stop %v still blocked after %s", args, bound)
		return nil
	}
}

// TestStopWithAHungFrontFallsBackToTheSignal: a front that accepts the
// connection and never answers must not block `flyball stop`: the HTTP
// attempt gives up after stopTimeout (its real value, 5 s) and
// --front-dir's runner.lock supplies the pid to signal.
func TestStopWithAHungFrontFallsBackToTheSignal(t *testing.T) {
	t.Setenv("FLYBALL_URL", hungListener(t))
	t.Setenv("FLYBALLD_URL", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	dir := t.TempDir()
	holdRunnerLock(t, dir, "pid "+strconv.Itoa(os.Getpid())+" rig blender\n")

	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	start := time.Now()
	if err := stopWithin(t, 10*time.Second, "--front-dir", dir); err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	t.Logf("returned after %s", time.Since(start).Round(time.Millisecond))
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("SIGUSR1 was not delivered after the front timed out")
	}
}

// TestStopResolvingThroughAHungDaemonFallsBack: with FLYBALLD_URL set and
// no NAME, the target is resolved by listing flyballd's runners -- that
// call is bounded too.
func TestStopResolvingThroughAHungDaemonFallsBack(t *testing.T) {
	shortStopTimeout(t)
	t.Setenv("FLYBALLD_URL", hungListener(t))
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	dir := t.TempDir()
	holdRunnerLock(t, dir, "pid "+strconv.Itoa(os.Getpid())+" rig blender\n")

	ch := make(chan os.Signal, 1)
	notifyUSR1(t, ch)

	if err := stopWithin(t, 10*time.Second, "--front-dir", dir); err != nil {
		t.Fatalf("runStopCommand: %v", err)
	}
	select {
	case <-ch:
	case <-time.After(2 * time.Second):
		t.Fatal("SIGUSR1 was not delivered after flyballd timed out")
	}
}

// TestStopAllWithAHungDaemonIsBounded: `flyball stop --all` against a
// flyballd that never answers its list returns an error, not a hang.
func TestStopAllWithAHungDaemonIsBounded(t *testing.T) {
	shortStopTimeout(t)
	t.Setenv("FLYBALLD_URL", hungListener(t))
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	if err := stopWithin(t, 10*time.Second, "--all"); err == nil {
		t.Fatal("expected an error: flyballd never answered, nothing was stopped")
	}
}
