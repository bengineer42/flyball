package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

// newRefusingProxy builds a reverse proxy pointed at a port nothing is
// listening on (dialing it always fails with ECONNREFUSED, the same as a
// runner that hasn't started yet), wired up with our quiet-startup
// ErrorHandler.
func newRefusingProxy(t *testing.T) (*httputil.ReverseProxy, string, *atomic.Bool) {
	t.Helper()

	// Grab a free port, then stop listening on it so a dial refuses.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("net.Listen: %v", err)
	}
	addr := ln.Addr().String()
	ln.Close()

	target, err := url.Parse("http://" + addr)
	if err != nil {
		t.Fatalf("url.Parse: %v", err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	var ready atomic.Bool
	proxy.ModifyResponse = func(*http.Response) error {
		ready.Store(true)
		return nil
	}
	proxy.ErrorHandler = quietStartupErrors(&ready)
	return proxy, addr, &ready
}

func doProxied(proxy *httputil.ReverseProxy) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/status", nil)
	proxy.ServeHTTP(rec, req)
	return rec
}

// TestServeUIQuietBeforeUpstreamAnswers checks that a connection-refused
// error, before the runner has ever answered a request, gets a plain
// error response and produces no log line -- the noisy-startup case this
// ErrorHandler exists to fix.
func TestServeUIQuietBeforeUpstreamAnswers(t *testing.T) {
	proxy, _, ready := newRefusingProxy(t)

	var logBuf bytes.Buffer
	restore := log.Writer()
	log.SetOutput(&logBuf)
	defer log.SetOutput(restore)

	rec := doProxied(proxy)

	if rec.Code != http.StatusBadGateway && rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("code = %d, want 502 or 503", rec.Code)
	}
	if logBuf.Len() != 0 {
		t.Fatalf("logged output before the runner ever answered: %q", logBuf.String())
	}
	if ready.Load() {
		t.Fatal("ready should still be false: nothing has answered yet")
	}
}

// TestServeUILogsAfterUpstreamHasAnswered checks that once the runner has
// answered a request at least once, a later connection-refused error (the
// runner having gone away again) is logged as before.
func TestServeUILogsAfterUpstreamHasAnswered(t *testing.T) {
	proxy, addr, ready := newRefusingProxy(t)

	// Bring the "runner" up on the same address and let the proxy reach it
	// once.
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatalf("net.Listen: %v", err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})}
	go srv.Serve(ln)
	defer srv.Close()

	if rec := doProxied(proxy); rec.Code != http.StatusOK {
		t.Fatalf("first request to the up runner: code = %d, want 200", rec.Code)
	}
	if !ready.Load() {
		t.Fatal("ready should be true once the runner has answered")
	}

	// Take the runner down again and confirm the next connection-refused
	// error is logged, not swallowed.
	srv.Close()
	ln.Close()

	var logBuf bytes.Buffer
	restore := log.Writer()
	log.SetOutput(&logBuf)
	defer log.SetOutput(restore)

	rec := doProxied(proxy)
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("code = %d, want 502", rec.Code)
	}
	if !strings.Contains(logBuf.String(), "proxy error") {
		t.Fatalf("expected a logged proxy error once the runner is known to be up, got %q", logBuf.String())
	}
}

// TestMain lets this test binary stand in for flyball-runner: run with
// FLYBALL_FAKE_RUNNER set, it serves GET /api/auth on 127.0.0.1 at
// FLYBALL_FAKE_PORT with the door FLYBALL_FAKE_DOOR names ("open" or
// "token"), writes FLYBALL_FAKE_RUNNER (a file) when it is stopped by
// SIGTERM, and exits on its own after FLYBALL_FAKE_LIFETIME.
func TestMain(m *testing.M) {
	if marker := os.Getenv("FLYBALL_FAKE_RUNNER"); marker != "" {
		os.Exit(fakeRunner(marker))
	}
	os.Exit(m.Run())
}

func fakeRunner(marker string) int {
	token := os.Getenv("FLYBALL_FAKE_DOOR") == "token"
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/auth", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"scheme": "anonymous", "level": "operate", "anonymous": "none",
			"password": false, "token": token,
		})
	})
	ln, err := net.Listen("tcp", "127.0.0.1:"+os.Getenv("FLYBALL_FAKE_PORT"))
	if err != nil {
		fmt.Fprintln(os.Stderr, "fake runner:", err)
		return 3
	}
	go http.Serve(ln, mux)
	lifetime, _ := time.ParseDuration(os.Getenv("FLYBALL_FAKE_LIFETIME"))
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM)
	select {
	case <-sigs:
		os.WriteFile(marker, []byte("stopped"), 0o644)
		return 0
	case <-time.After(lifetime):
		return 0
	}
}

func freePort(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	return fmt.Sprint(ln.Addr().(*net.TCPAddr).Port)
}

// runFake runs `flyball run RIG --serve-ui ADDR [extra...]` against the
// fake runner and returns runDirect's error, what it wrote to stderr, how
// long it took and whether the runner was stopped by a signal.
func runFake(t *testing.T, door, rigYAML, addr string, lifetime time.Duration, extra ...string) (error, string, time.Duration, bool) {
	t.Helper()
	dir := t.TempDir()
	rig := filepath.Join(dir, "rig.yaml")
	if err := os.WriteFile(rig, []byte(rigYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(dir, "stopped")
	port := freePort(t)
	t.Setenv("FLYBALL_FAKE_RUNNER", marker)
	t.Setenv("FLYBALL_FAKE_PORT", port)
	t.Setenv("FLYBALL_FAKE_DOOR", door)
	t.Setenv("FLYBALL_FAKE_LIFETIME", lifetime.String())
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	old := runnerCommand
	runnerCommand = self
	defer func() { runnerCommand = old }()

	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	stderr := os.Stderr
	os.Stderr = w
	start := time.Now()
	args := append([]string{rig, "--serve-ui", addr, "--port", port}, extra...)
	runErr := runDirect(args)
	took := time.Since(start)
	os.Stderr = stderr
	w.Close()
	out, _ := io.ReadAll(r)
	_, statErr := os.Stat(marker)
	return runErr, string(out), took, statErr == nil
}

// An open runner behind a front on every interface: the front refuses,
// stops the runner and exits non-zero -- well before the runner would
// have exited by itself.
func TestServeUIRefusesAnOpenRunnerBeyondLoopback(t *testing.T) {
	err, out, took, stopped := runFake(t, "open", "name: t\n", "0.0.0.0:0", 30*time.Second)
	if err == nil || !strings.Contains(err.Error(), "open runner") || !strings.Contains(err.Error(), "--insecure-open") {
		t.Fatalf("runDirect = %v, want the open-runner refusal (stderr: %s)", err, out)
	}
	if !stopped {
		t.Fatal("the runner was not stopped")
	}
	if took > 15*time.Second {
		t.Fatalf("took %s: the runner ran out its lifetime instead of being stopped", took)
	}
	if strings.Contains(out, "serving UI on") {
		t.Fatalf("the UI was served: %s", out)
	}
}

// With a token the front serves, and warns once that it is plain HTTP.
func TestServeUIServesARunnerWithATokenAndWarnsOfCleartext(t *testing.T) {
	err, out, _, stopped := runFake(t, "token", "name: t\n", "0.0.0.0:0", 1500*time.Millisecond)
	if err != nil || stopped {
		t.Fatalf("runDirect = %v (stopped %v), want a normal run (stderr: %s)", err, stopped, out)
	}
	if strings.Count(out, "unencrypted") != 1 || !strings.Contains(out, "serving UI on") {
		t.Fatalf("stderr = %q, want one cleartext warning and the UI served", out)
	}
}

// The explicit opt-in, as a flag or in the rig file, lets an open runner out, with a warning.
func TestServeUIOpenRunnerWithTheOptIn(t *testing.T) {
	for name, c := range map[string]struct {
		rig   string
		extra []string
	}{
		"flag": {"name: t\n", []string{"--insecure-open"}},
		"file": {"name: t\nrunner:\n  auth:\n    insecure_open: true\n", nil},
	} {
		t.Run(name, func(t *testing.T) {
			err, out, _, stopped := runFake(t, "open", c.rig, "0.0.0.0:0", 1500*time.Millisecond, c.extra...)
			if err != nil || stopped {
				t.Fatalf("runDirect = %v (stopped %v): %s", err, stopped, out)
			}
			if !strings.Contains(out, "OPEN runner") {
				t.Fatalf("stderr = %q, want the open warning", out)
			}
		})
	}
}

// On loopback nothing changes: an open runner is served, no warning.
func TestServeUILoopbackServesAnOpenRunner(t *testing.T) {
	err, out, _, stopped := runFake(t, "open", "name: t\n", "127.0.0.1:0", 1500*time.Millisecond)
	if err != nil || stopped || strings.Contains(out, "unencrypted") || strings.Contains(out, "OPEN") {
		t.Fatalf("runDirect = %v (stopped %v), stderr %q", err, stopped, out)
	}
}
