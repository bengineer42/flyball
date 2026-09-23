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
	"os/exec"
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
	if os.Getenv("FLYBALL_FAKE_UV") != "" {
		os.Exit(fakeUV())
	}
	if marker := os.Getenv("FLYBALL_FAKE_RUNNER"); marker != "" {
		os.Exit(fakeRunner(marker))
	}
	os.Exit(m.Run())
}

// fakeUV stands in for `uv run ... flyball-runner ARGS`: it runs the fake
// runner (this binary again) as its child and waits for it, ignoring
// SIGINT as uv does when it has no terminal -- uv leaves SIGINT to the
// terminal, which sends it to the whole foreground process group.
func fakeUV() int {
	signal.Ignore(syscall.SIGINT)
	self, _ := os.Executable()
	args := os.Args[1:]
	for i, a := range args { // drop "run --project DIR flyball-runner"
		if a == "flyball-runner" {
			args = args[i+1:]
			break
		}
	}
	child := exec.Command(self, args...)
	child.Env = append(os.Environ(), "FLYBALL_FAKE_UV=")
	child.Stdout, child.Stderr = os.Stdout, os.Stderr
	if err := child.Run(); err != nil {
		return 1
	}
	return 0
}

func fakeRunner(marker string) int {
	token := os.Getenv("FLYBALL_FAKE_DOOR") == "token"
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/auth", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"scheme": "anonymous", "level": "operate", "anonymous": "none",
			"password": false, "token": token, "exposure": nil,
		})
	})
	port := os.Getenv("FLYBALL_FAKE_PORT") // what the rig file would say
	for i, a := range os.Args {
		if a == "--port" && i+1 < len(os.Args) {
			port = os.Args[i+1] // the command line wins, as for flyball-runner
		}
	}
	ln, err := net.Listen("tcp", "127.0.0.1:"+port)
	if err != nil {
		fmt.Fprintln(os.Stderr, "fake runner:", err)
		return 3
	}
	go http.Serve(ln, mux)
	lifetime, _ := time.ParseDuration(os.Getenv("FLYBALL_FAKE_LIFETIME"))
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, syscall.SIGINT)
	select {
	case sig := <-sigs:
		os.WriteFile(marker, []byte(sig.String()), 0o644)
		return 0
	case <-time.After(lifetime):
		return 0
	}
}

// The fixed ports these tests use: the fake runner, and the front.
const (
	fakeRunnerPort = "18356"
	frontPort      = "18357"
)

// lanAddress is this machine's address on its default route, or "" (no
// packet is sent).
func lanAddress() string {
	c, err := net.Dial("udp", "192.0.2.1:9")
	if err != nil {
		return ""
	}
	defer c.Close()
	host, _, _ := net.SplitHostPort(c.LocalAddr().String())
	if net.ParseIP(host).IsLoopback() {
		return ""
	}
	return host
}

// seen is what a test saw of the front while it ran: /api/auth through it
// on loopback, and whether the machine's LAN address reached it.
type seen struct {
	auth       map[string]any
	lanReached bool
}

// runFake runs `flyball run RIG --serve-ui HOST:frontPort [extra...]`
// against the fake runner and returns runDirect's error, what it wrote to
// stderr, how long it took, whether the runner was stopped by a signal,
// and what the front looked like while it ran.
func runFake(t *testing.T, door, rigYAML, host string, lifetime time.Duration, extra ...string) (error, string, time.Duration, bool, seen) {
	t.Helper()
	return runFakeArgs(t, door, rigYAML, lifetime, append([]string{"--serve-ui", net.JoinHostPort(host, frontPort), "--port", fakeRunnerPort}, extra...)...)
}

// runFakeArgs is runFake with the whole of `flyball run RIG`'s flags given.
func runFakeArgs(t *testing.T, door, rigYAML string, lifetime time.Duration, flags ...string) (error, string, time.Duration, bool, seen) {
	t.Helper()
	dir := t.TempDir()
	rig := filepath.Join(dir, "rig.yaml")
	if err := os.WriteFile(rig, []byte(rigYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(dir, "stopped")
	t.Setenv("FLYBALL_FAKE_RUNNER", marker)
	if os.Getenv("FLYBALL_FAKE_PORT") == "" { // a test may have set what the rig file says
		t.Setenv("FLYBALL_FAKE_PORT", fakeRunnerPort)
	}
	t.Setenv("FLYBALL_FAKE_DOOR", door)
	t.Setenv("FLYBALL_FAKE_LIFETIME", lifetime.String())
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	old := runnerCommand
	runnerCommand = self
	defer func() { runnerCommand = old }()

	var saw seen
	looked := make(chan struct{})
	go func() {
		defer close(looked)
		client := &http.Client{Timeout: 300 * time.Millisecond}
		for end := time.Now().Add(lifetime); time.Now().Before(end); time.Sleep(50 * time.Millisecond) {
			resp, err := client.Get("http://127.0.0.1:" + frontPort + "/api/auth")
			if err != nil {
				continue
			}
			json.NewDecoder(resp.Body).Decode(&saw.auth)
			resp.Body.Close()
			if lan := lanAddress(); lan != "" {
				if c, err := net.DialTimeout("tcp", net.JoinHostPort(lan, frontPort), 300*time.Millisecond); err == nil {
					saw.lanReached = true
					c.Close()
				}
			}
			return
		}
	}()

	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	stderr := os.Stderr
	os.Stderr = w
	start := time.Now()
	args := append([]string{rig}, flags...)
	runErr := runDirect(args)
	took := time.Since(start)
	os.Stderr = stderr
	w.Close()
	out, _ := io.ReadAll(r)
	<-looked
	_, statErr := os.Stat(marker)
	return runErr, string(out), took, statErr == nil, saw
}

func exposureOf(t *testing.T, s seen) map[string]any {
	t.Helper()
	e, _ := s.auth["exposure"].(map[string]any)
	if e == nil {
		t.Fatalf("/api/auth through the front has no exposure: %v", s.auth)
	}
	return e
}

// An open runner behind a front asked for every interface: an auth
// misconfiguration removes exposure, never operation -- the runner keeps
// running, the front serves on loopback only, says why once, and
// /api/auth says so.
func TestServeUIServesAnOpenRunnerOnLoopbackOnly(t *testing.T) {
	err, out, _, stopped, saw := runFake(t, "open", "name: t\n", "0.0.0.0", 2*time.Second)
	if err != nil || stopped {
		t.Fatalf("runDirect = %v (stopped %v), want the runner left running (stderr: %s)", err, stopped, out)
	}
	if strings.Count(out, "WARNING") != 1 || !strings.Contains(out, "127.0.0.1:"+frontPort) || !strings.Contains(out, "--insecure-open") {
		t.Fatalf("stderr = %q, want one warning naming 127.0.0.1 and the fix", out)
	}
	if !strings.Contains(out, "serving UI on 127.0.0.1:"+frontPort) {
		t.Fatalf("stderr = %q, want the UI served on loopback", out)
	}
	if saw.lanReached {
		t.Fatal("the front was reachable on the LAN address")
	}
	e := exposureOf(t, saw)
	if e["restricted"] != true || e["open_network"] != false || e["requested"] != "0.0.0.0:"+frontPort {
		t.Fatalf("exposure = %v, want restricted from 0.0.0.0", e)
	}
}

// With a token the front serves where asked, and warns once that it is plain HTTP.
func TestServeUIServesARunnerWithATokenAndWarnsOfCleartext(t *testing.T) {
	err, out, _, stopped, _ := runFake(t, "token", "name: t\n", "0.0.0.0", 1500*time.Millisecond)
	if err != nil || stopped {
		t.Fatalf("runDirect = %v (stopped %v), want a normal run (stderr: %s)", err, stopped, out)
	}
	if strings.Count(out, "unencrypted") != 1 || !strings.Contains(out, "serving UI on 0.0.0.0:"+frontPort) {
		t.Fatalf("stderr = %q, want one cleartext warning and the UI served", out)
	}
}

// The explicit opt-in, as a flag or in the environment -- never the rig
// file -- serves an open runner where asked, with a warning, and /api/auth
// says it is open to the network.
func TestServeUIOpenRunnerWithTheOptIn(t *testing.T) {
	for name, c := range map[string]struct {
		env   string
		extra []string
	}{
		"flag": {"", []string{"--insecure-open"}},
		"env":  {"1", nil},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("FLYBALL_INSECURE_OPEN", c.env)
			err, out, _, stopped, saw := runFake(t, "open", "name: t\n", "0.0.0.0", 1500*time.Millisecond, c.extra...)
			if err != nil || stopped {
				t.Fatalf("runDirect = %v (stopped %v): %s", err, stopped, out)
			}
			if !strings.Contains(out, "OPEN runner") || !strings.Contains(out, "serving UI on 0.0.0.0:"+frontPort) {
				t.Fatalf("stderr = %q, want the open warning", out)
			}
			if e := exposureOf(t, saw); e["open_network"] != true {
				t.Fatalf("exposure = %v, want open_network", e)
			}
		})
	}
}

// A rig-file key is not an opt-in: a file can be pasted or `extends`ed.
func TestServeUIRigFileCannotOptIn(t *testing.T) {
	t.Setenv("FLYBALL_INSECURE_OPEN", "")
	rig := "name: t\nrunner:\n  auth:\n    insecure_open: true\n"
	err, out, _, _, saw := runFake(t, "open", rig, "0.0.0.0", 1500*time.Millisecond)
	if err != nil || !strings.Contains(out, "serving UI on 127.0.0.1:"+frontPort) {
		t.Fatalf("runDirect = %v, stderr %q: want loopback only", err, out)
	}
	if saw.lanReached {
		t.Fatal("the front was reachable on the LAN address")
	}
}

// On loopback nothing changes: an open runner is served, no warning.
func TestServeUILoopbackServesAnOpenRunner(t *testing.T) {
	err, out, _, stopped, saw := runFake(t, "open", "name: t\n", "127.0.0.1", 1500*time.Millisecond)
	if err != nil || stopped || strings.Contains(out, "WARNING") {
		t.Fatalf("runDirect = %v (stopped %v), stderr %q", err, stopped, out)
	}
	if saw.auth["exposure"] != nil {
		t.Fatalf("exposure = %v, want the runner's own (none)", saw.auth["exposure"])
	}
}

// The front proxies to the port the runner really serves on: the rig
// file's runner.port when no --port is given, and --port (passed on to the
// runner) when it is -- never a fixed 8000.
func TestServeUIProxiesToTheRunnersOwnPort(t *testing.T) {
	for name, c := range map[string]struct {
		rig      string
		fakePort string // what the fake runner would read from its rig file
		flags    []string
	}{
		"runner.port": {"name: t\nrunner:\n  port: " + fakeRunnerPort + "\n", fakeRunnerPort, nil},
		"--port":      {"name: t\n", "1", []string{"--port", fakeRunnerPort}},
	} {
		t.Run(name, func(t *testing.T) {
			flags := append([]string{"--serve-ui", "127.0.0.1:" + frontPort}, c.flags...)
			t.Setenv("FLYBALL_FAKE_PORT", c.fakePort)
			err, out, _, _, saw := runFakeArgs(t, "open", c.rig, 1500*time.Millisecond, flags...)
			if err != nil {
				t.Fatalf("runDirect = %v: %s", err, out)
			}
			if saw.auth == nil || !strings.Contains(out, "proxying to runner on 127.0.0.1:"+fakeRunnerPort) {
				t.Fatalf("the front did not reach the runner on %s: stderr %q, /api/auth %v", fakeRunnerPort, out, saw.auth)
			}
		})
	}
}

// `flyball run --uv` stopped from outside a terminal (a script, a service
// manager): the SIGINT it is sent must reach the runner, although uv
// ignores SIGINT -- it leaves it to the terminal's process group.
func TestRunUVDeliversTheStopToTheRunner(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	old := uvCommand
	uvCommand = self
	defer func() { uvCommand = old }()
	t.Setenv("FLYBALL_FAKE_UV", "1")
	dir := t.TempDir()
	rig := filepath.Join(dir, "rig.yaml")
	os.WriteFile(rig, []byte("name: t\n"), 0o644)
	marker := filepath.Join(dir, "stopped")
	t.Setenv("FLYBALL_FAKE_RUNNER", marker)
	t.Setenv("FLYBALL_FAKE_PORT", fakeRunnerPort)
	t.Setenv("FLYBALL_FAKE_DOOR", "open")
	t.Setenv("FLYBALL_FAKE_LIFETIME", "20s")

	done := make(chan error, 1)
	start := time.Now()
	go func() { done <- runDirect([]string{rig, "--uv", "--port", fakeRunnerPort}) }()
	client := &http.Client{Timeout: 300 * time.Millisecond}
	for up := false; !up; time.Sleep(50 * time.Millisecond) {
		if time.Since(start) > 10*time.Second {
			t.Fatal("the fake runner never answered")
		}
		if resp, err := client.Get("http://127.0.0.1:" + fakeRunnerPort + "/api/auth"); err == nil {
			resp.Body.Close()
			up = true
		}
	}
	syscall.Kill(os.Getpid(), syscall.SIGINT) // as `kill -INT <flyball>` would
	select {
	case err := <-done:
		got, _ := os.ReadFile(marker)
		if string(got) != "interrupt" {
			t.Fatalf("runDirect = %v; the runner saw %q, want interrupt", err, got)
		}
	case <-time.After(8 * time.Second):
		t.Fatal("the runner kept running: the SIGINT never reached it")
	}
}
