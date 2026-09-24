package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/fronttest"
)

// TestMain lets this test binary stand in for flyball-runner (and for uv).
// With FLYBALL_FAKE_RUNNER set (a marker file) it is a fronted runner: it
// appends its argv to FLYBALL_FAKE_ARGS, serves the endpoint its
// --front-dir names (fronttest), writes the marker when stopped by a
// signal, and exits on its own after FLYBALL_FAKE_LIFETIME. With
// FLYBALL_FAKE_EXIT=CODE:N its first N spawns exit CODE at once; with
// FLYBALL_FAKE_CHATTER=DURATION it prints a line to stdout that often.
// With FLYBALL_TEST_RUN_DIRECT set (newline-separated arguments) it is
// `flyball run` itself: runDirect, signals and all, against the fake runner.
func TestMain(m *testing.M) {
	if args := os.Getenv("FLYBALL_TEST_RUN_DIRECT"); args != "" {
		os.Unsetenv("FLYBALL_TEST_RUN_DIRECT") // the runner it spawns is the fake runner
		runnerCommand, _ = os.Executable()
		os.Exit(runExitCode(runDirect(strings.Split(args, "\n"))))
	}
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
		var ee *exec.ExitError
		if errors.As(err, &ee) && ee.ExitCode() > 0 {
			return ee.ExitCode()
		}
		return 1
	}
	return 0
}

func fakeRunner(marker string) int {
	argsFile := os.Getenv("FLYBALL_FAKE_ARGS")
	f, _ := os.OpenFile(argsFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	fmt.Fprintln(f, strings.Join(os.Args[1:], " "))
	f.Close()
	spawns := len(readLines(argsFile))
	if code, times, ok := strings.Cut(os.Getenv("FLYBALL_FAKE_EXIT"), ":"); ok {
		n, _ := strconv.Atoi(times)
		if spawns <= n {
			c, _ := strconv.Atoi(code)
			return c
		}
	}
	dir := ""
	for i, a := range os.Args {
		if a == "--front-dir" && i+1 < len(os.Args) {
			dir = os.Args[i+1]
		}
	}
	if dir == "" {
		fmt.Fprintln(os.Stderr, "fake runner: no --front-dir")
		return 2
	}
	r, err := fronttest.FromFrontDir(dir, "", "t")
	if err != nil {
		fmt.Fprintln(os.Stderr, "fake runner:", err)
		return 4
	}
	defer r.Close()
	if every, err := time.ParseDuration(os.Getenv("FLYBALL_FAKE_CHATTER")); err == nil {
		go func() {
			for range time.Tick(every) {
				fmt.Println("fake runner chatter")
			}
		}()
	}
	lifetime, _ := time.ParseDuration(os.Getenv("FLYBALL_FAKE_LIFETIME"))
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM, syscall.SIGINT)
	if os.Getenv("FLYBALL_FAKE_STUBBORN") != "" {
		// A runner whose shutdown hangs: it records every stop signal (one
		// line each) and never exits on one -- only SIGKILL ends it.
		os.WriteFile(marker+".pid", []byte(strconv.Itoa(os.Getpid())), 0o644)
		for sig := range sigs {
			f, _ := os.OpenFile(marker, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
			fmt.Fprintln(f, sig.String())
			f.Close()
		}
	}
	select {
	case sig := <-sigs:
		os.WriteFile(marker, []byte(sig.String()), 0o644)
		return 0
	case <-time.After(lifetime):
		return 0
	}
}

func readLines(path string) []string {
	b, _ := os.ReadFile(path)
	s := strings.TrimSpace(string(b))
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}

// A run under test: runDirect's arguments, what it printed, how it ended.
type fakeRun struct {
	t      *testing.T
	dir    string
	marker string
	args   string
	sigs   chan os.Signal
	done   chan error
	addrs  chan string
	at     string
	mu     sync.Mutex
	out    bytes.Buffer
}

// addr is where the front listens (it is asked for port 0).
func (r *fakeRun) addr() string {
	r.t.Helper()
	if r.at != "" {
		return r.at
	}
	select {
	case r.at = <-r.addrs:
		return r.at
	case <-time.After(10 * time.Second):
		r.t.Fatalf("the front never listened; output:\n%s", r.output())
		return ""
	}
}

func (r *fakeRun) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.out.Write(p)
}

func (r *fakeRun) output() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.out.String()
}

// fakeEnv points the runtime and state dirs at the test and the runner
// command at this binary.
func fakeEnv(t *testing.T) (dir string) {
	t.Helper()
	dir, err := os.MkdirTemp("", "fr")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	os.Mkdir(filepath.Join(dir, "run"), 0o700)
	t.Setenv("XDG_RUNTIME_DIR", filepath.Join(dir, "run"))
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))
	t.Setenv("FLYBALL_INSECURE_OPEN", "")
	t.Setenv("FLYBALL_FAKE_RUNNER", filepath.Join(dir, "stopped"))
	t.Setenv("FLYBALL_FAKE_ARGS", filepath.Join(dir, "args"))
	t.Setenv("FLYBALL_FAKE_LIFETIME", "60s")
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	old, oldOut, oldListen := runnerCommand, runOut, onListen
	runnerCommand = self
	t.Cleanup(func() { runnerCommand, runOut, onListen = old, oldOut, oldListen })
	return dir
}

// startRun starts `flyball run RIG flags...` against the fake runner.
func startRun(t *testing.T, rigYAML string, flags ...string) *fakeRun {
	t.Helper()
	dir := fakeEnv(t)
	rig := filepath.Join(dir, "rig.yaml")
	if err := os.WriteFile(rig, []byte(rigYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	r := &fakeRun{t: t, dir: dir, marker: filepath.Join(dir, "stopped"), args: filepath.Join(dir, "args"),
		sigs: make(chan os.Signal, 2), done: make(chan error, 1), addrs: make(chan string, 4)}
	runOut = r
	r.listen()
	go func() { r.done <- run(append([]string{rig}, flags...), r.sigs) }()
	t.Cleanup(func() { r.stop() })
	return r
}

// listen makes this run the one told where the front listens.
func (r *fakeRun) listen() {
	onListen = func(a net.Addr) {
		select {
		case r.addrs <- a.String():
		default:
		}
	}
}

// freePort is a loopback port nothing listens on (as far as it can tell).
func freePort(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	_, port, _ := net.SplitHostPort(ln.Addr().String())
	return port
}

// stop sends SIGTERM and waits for the run to end.
func (r *fakeRun) stop() error {
	select {
	case err := <-r.done:
		r.done <- err
		return err
	default:
	}
	r.sigs <- syscall.SIGTERM
	select {
	case err := <-r.done:
		r.done <- err
		return err
	case <-time.After(15 * time.Second):
		r.t.Fatalf("flyball run did not stop; output:\n%s", r.output())
		return nil
	}
}

var testClient = &http.Client{Timeout: 3 * time.Second}

// echoAt waits (up to 10 s) for GET http://addr/api/echo through the front
// to answer 200, and returns what the runner saw.
func echoAt(t *testing.T, r *fakeRun, addr string, until func(fronttest.Echo) bool) fronttest.Echo {
	t.Helper()
	var last string
	for end := time.Now().Add(10 * time.Second); time.Now().Before(end); time.Sleep(50 * time.Millisecond) {
		resp, err := testClient.Get("http://" + addr + "/api/echo")
		if err != nil {
			last = err.Error()
			continue
		}
		var e fronttest.Echo
		err = json.NewDecoder(resp.Body).Decode(&e)
		resp.Body.Close()
		last = resp.Status
		if resp.StatusCode == 200 && err == nil && (until == nil || until(e)) {
			return e
		}
	}
	t.Fatalf("no 200 through %s (last: %s); output:\n%s", addr, last, r.output())
	return fronttest.Echo{}
}

// `flyball run` always fronts: the runner is spawned with --front-dir (a
// 0700 dir with key, aud and endpoint at 0600), binds only the endpoint
// there, and is reached through the front with a principal for aud.
func TestRunFrontsTheRunner(t *testing.T) {
	port := freePort(t)
	r := startRun(t, "name: t\n", "--listen", "127.0.0.1:0", "--port", port)
	addr := r.addr()
	e := echoAt(t, r, addr, nil)
	if !strings.HasPrefix(e.Claims.Aud, "run-") || len(e.Claims.Aud) != 12 || e.Claims.Sub != "local:console" ||
		!strings.Contains(strings.Join(e.Claims.Scp, ","), "operate") {
		t.Fatalf("principal = %+v", e.Claims)
	}
	argv := readLines(r.args)
	if len(argv) != 1 || !strings.Contains(argv[0], "--front-dir ") {
		t.Fatalf("runner argv = %q, want one spawn with --front-dir", argv)
	}
	fields := strings.Fields(argv[0])
	var dir string
	for i, a := range fields {
		if a == "--front-dir" {
			dir = fields[i+1]
		}
	}
	fi, err := os.Stat(dir)
	if err != nil || fi.Mode().Perm() != 0o700 {
		t.Fatalf("front-dir %s: %v %v", dir, fi, err)
	}
	for _, name := range []string{"key", "aud", "endpoint"} {
		fi, err := os.Stat(filepath.Join(dir, name))
		if err != nil || fi.Mode().Perm() != 0o600 {
			t.Fatalf("%s: %v %v", name, fi, err)
		}
	}
	ep, _ := os.ReadFile(filepath.Join(dir, "endpoint"))
	if !strings.HasPrefix(string(ep), "unix:"+dir+"/sock") {
		t.Fatalf("endpoint = %q, want the socket in the front-dir", ep)
	}
	if c, err := net.DialTimeout("tcp", "127.0.0.1:"+port, 300*time.Millisecond); err == nil {
		c.Close()
		t.Fatal("something listens on the runner's --port: the runner binds only its socket")
	}
	if err := r.stop(); err != nil {
		t.Fatalf("run = %v", err)
	}
	if got, _ := os.ReadFile(r.marker); string(got) != "terminated" {
		t.Fatalf("the runner saw %q, want SIGTERM", got)
	}
	if c, err := net.DialTimeout("tcp", addr, 300*time.Millisecond); err == nil {
		c.Close()
		t.Fatal("the front still listens after the run ended")
	}
	// Tokens and audit live in the front's state dir.
	matches, _ := filepath.Glob(filepath.Join(r.dir, "state", "flyball", "front-*", "audit.jsonl"))
	if len(matches) != 1 {
		t.Fatalf("audit files: %v", matches)
	}
}

// --serve-ui is an alias of --listen.
func TestRunServeUIIsAnAliasOfListen(t *testing.T) {
	r := startRun(t, "name: t\n", "--serve-ui", "127.0.0.1:0")
	echoAt(t, r, r.addr(), nil)
}

// runner.front.listen, and the deprecated runner.run.serve_ui (with a
// warning), say where the front listens.
func TestRunListensWhereTheRigFileSays(t *testing.T) {
	r := startRun(t, "name: t\nrunner:\n  front:\n    listen: 127.0.0.1:0\n")
	echoAt(t, r, r.addr(), nil)
	r.stop()

	r = startRun(t, "name: t\nrunner:\n  run:\n    serve_ui: 127.0.0.1:0\n")
	echoAt(t, r, r.addr(), nil)
	if !strings.Contains(r.output(), "runner.run") {
		t.Fatalf("no deprecation warning: %s", r.output())
	}
}

// D-028: a front that cannot serve what it was asked falls back to the
// local shape on loopback with a banner, and the rig is still served.
func TestRunBadFrontFallsBackAndTheRigRuns(t *testing.T) {
	for name, front := range map[string]string{
		"local beyond loopback": "listen: 0.0.0.0:0",
		"unreadable":            "listen: 0.0.0.0:0\n    auth: [local]",
		"unknown key":           "listen: 0.0.0.0:0\n    lisen: x",
		"sso":                   "listen: 0.0.0.0:0\n    auth: sso",
		"plaintext password":    "listen: 0.0.0.0:0\n    auth: password\n    password: hunter2",
	} {
		t.Run(name, func(t *testing.T) {
			r := startRun(t, "name: t\nrunner:\n  front:\n    "+front+"\n")
			addr := r.addr()
			host, port, _ := net.SplitHostPort(addr)
			if host != "127.0.0.1" {
				t.Fatalf("the front listens on %s, want loopback", addr)
			}
			e := echoAt(t, r, addr, nil)
			if e.Claims.Sub != "local:console" {
				t.Fatalf("principal %+v", e.Claims)
			}
			if !strings.Contains(r.output(), "D-028") || !strings.Contains(r.output(), "on 127.0.0.1:0 only") {
				t.Fatalf("no banner: %s", r.output())
			}
			if lan := lanAddress(); lan != "" {
				if c, err := net.DialTimeout("tcp", net.JoinHostPort(lan, port), 300*time.Millisecond); err == nil {
					c.Close()
					t.Fatal("the front is reachable on the LAN address")
				}
			}
		})
	}
}

// --insecure-open (per run, never a file key) serves the local shape where
// asked, with a warning.
func TestRunInsecureOpen(t *testing.T) {
	r := startRun(t, "name: t\n", "--listen", "0.0.0.0:0", "--insecure-open")
	_, port, _ := net.SplitHostPort(r.addr())
	echoAt(t, r, "127.0.0.1:"+port, nil)
	if !strings.Contains(r.output(), "OPEN") {
		t.Fatalf("no open warning: %s", r.output())
	}
	for _, line := range readLines(r.args) {
		if strings.Contains(line, "--insecure-open") {
			t.Fatalf("the runner got --insecure-open: %q", line)
		}
	}
}

// kill -9 the runner: it is spawned again with a fresh key, the same aud
// and argv, and the front answers 200 again.
func TestRunRespawnsAfterKill9(t *testing.T) {
	r := startRun(t, "name: t\n", "--listen", "127.0.0.1:0")
	first := echoAt(t, r, r.addr(), nil)
	syscall.Kill(first.Pid, syscall.SIGKILL)
	second := echoAt(t, r, r.addr(), func(e fronttest.Echo) bool { return e.Pid != first.Pid })
	if second.Key == first.Key || second.Claims.Aud != first.Claims.Aud {
		t.Fatalf("respawn: key %s -> %s, aud %s -> %s; want a fresh key and the same aud", first.Key, second.Key, first.Claims.Aud, second.Claims.Aud)
	}
	argv := readLines(r.args)
	if len(argv) != 2 || argv[0] != argv[1] {
		t.Fatalf("argv = %q, want two identical spawns", argv)
	}
}

// Exit 2 (bad rig file, or a runner too old for --front-dir) and 3 (rig
// busy) end the run; exit 4 is answered by one rewrite and respawn.
func TestRunExitCodes(t *testing.T) {
	for _, c := range []struct {
		exit   string
		spawns int
		ends   bool
		says   string
	}{
		{"2:9", 1, true, "exit status 2"},
		{"3:9", 1, true, "busy"},
		{"4:9", 2, true, "front-dir"},
		{"4:1", 2, false, ""},
	} {
		t.Run(c.exit, func(t *testing.T) {
			t.Setenv("FLYBALL_FAKE_EXIT", c.exit)
			r := startRun(t, "name: t\n", "--listen", "127.0.0.1:0")
			if c.ends {
				select {
				case err := <-r.done:
					r.done <- err
					if err == nil || !strings.Contains(err.Error()+r.output(), c.says) {
						t.Fatalf("run = %v, want an error saying %q; output:\n%s", err, c.says, r.output())
					}
				case <-time.After(10 * time.Second):
					t.Fatal("the run did not end")
				}
			} else {
				echoAt(t, r, r.addr(), nil)
			}
			if got := len(readLines(r.args)); got != c.spawns {
				t.Fatalf("%d spawns, want %d", got, c.spawns)
			}
		})
	}
}

// A second `flyball run` of the same rig file finds the first's runner
// holding the front-dir, and stops without touching its key. The refusal
// names the pid holding it and how to end that runner -- not `flyball
// stop`, which stops the rig and leaves the runner running.
func TestRunTwiceRefusesTheLiveFrontDir(t *testing.T) {
	r := startRun(t, "name: t\n", "--listen", "127.0.0.1:0")
	first := echoAt(t, r, r.addr(), nil)
	rig := filepath.Join(r.dir, "rig.yaml")
	runOut, onListen = &syncBuffer{}, nil
	err := run([]string{rig, "--listen", "127.0.0.1:0"}, make(chan os.Signal))
	if err == nil || !strings.Contains(err.Error(), "runner.lock") {
		t.Fatalf("second run = %v, want refused on runner.lock", err)
	}
	if !strings.Contains(err.Error(), fmt.Sprint(first.Pid)) || !strings.Contains(err.Error(), fmt.Sprintf("kill %d", first.Pid)) ||
		strings.Contains(err.Error(), "`flyball stop "+rig+"` to stop it") {
		t.Fatalf("second run = %v, want the pid %d and `kill %d`, not `flyball stop` as the way to end it", err, first.Pid, first.Pid)
	}
	runOut = r
	again := echoAt(t, r, r.addr(), nil)
	if again.Key != first.Key || again.Pid != first.Pid {
		t.Fatal("the second run disturbed the first runner")
	}
}

// D-038: bare (no --uv) also puts the runner in a process group of its
// own, the same as --uv, so a hangup's SIGHUP -- delivered to the whole
// foreground group when there is a controlling terminal -- never reaches
// it (the front ignores its own copy; see TestRunSurvivesSIGHUP).
func TestRunBareRunnerHasItsOwnProcessGroup(t *testing.T) {
	r := startRun(t, "name: t\n", "--listen", "127.0.0.1:0")
	first := echoAt(t, r, r.addr(), nil)
	pgid, err := syscall.Getpgid(first.Pid)
	if err != nil {
		t.Fatal(err)
	}
	if pgid != first.Pid {
		t.Fatalf("runner pid %d is in group %d, want its own (Setpgid)", first.Pid, pgid)
	}
	if pgid == syscall.Getpgrp() {
		t.Fatalf("the runner shares this test process's group %d", pgid)
	}
}

// D-038: `flyball run` also writes its own output and the runner's to
// <state dir>/run.log (frontwire.RunDir), 0600 in a 0700 directory, so
// what happened is not lost with a dead terminal.
func TestRunWritesRunLog(t *testing.T) {
	r := startRun(t, "name: t\n", "--listen", "127.0.0.1:0")
	echoAt(t, r, r.addr(), nil)
	matches, err := filepath.Glob(filepath.Join(r.dir, "state", "flyball", "front-*"))
	if err != nil || len(matches) != 1 {
		t.Fatalf("front state dir: %v %v", matches, err)
	}
	fi, err := os.Stat(matches[0])
	if err != nil || fi.Mode().Perm() != 0o700 {
		t.Fatalf("front state dir %s: %v %v, want 0700", matches[0], fi, err)
	}
	logPath := filepath.Join(matches[0], "run.log")
	lfi, err := os.Stat(logPath)
	if err != nil || lfi.Mode().Perm() != 0o600 {
		t.Fatalf("run.log %s: %v %v, want 0600", logPath, lfi, err)
	}
	if err := r.stop(); err != nil {
		t.Fatalf("run = %v", err)
	}
	got, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(got), "serving rig t on") {
		t.Fatalf("run.log = %q, want the front's own banner lines", got)
	}
}

// D-038: SIGHUP -- what a dropped terminal sends the foreground process
// group -- never stops `flyball run` or its runner: the front logs one
// line and keeps serving, the runner (already in its own group, see
// TestRunBareRunnerHasItsOwnProcessGroup) never sees it at all.
func TestRunSurvivesSIGHUP(t *testing.T) {
	r := startRun(t, "name: t\n", "--listen", "127.0.0.1:0")
	before := echoAt(t, r, r.addr(), nil)
	if err := syscall.Kill(os.Getpid(), syscall.SIGHUP); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(5 * time.Second)
	for !strings.Contains(r.output(), "terminal hung up") {
		if time.Now().After(deadline) {
			t.Fatalf("no \"terminal hung up\" line within 5s; output:\n%s", r.output())
		}
		time.Sleep(20 * time.Millisecond)
	}
	after := echoAt(t, r, r.addr(), nil)
	if after.Pid != before.Pid {
		t.Fatalf("the runner was respawned (pid %d -> %d): SIGHUP reached it", before.Pid, after.Pid)
	}
	if err := r.stop(); err != nil {
		t.Fatalf("run did not stop cleanly by SIGTERM after a hangup: %v", err)
	}
}

// `flyball run --uv` stopped from outside a terminal: the signal reaches
// the runner, although uv ignores SIGINT.
func TestRunUVDeliversTheStopToTheRunner(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	old := uvCommand
	uvCommand = self
	defer func() { uvCommand = old }()
	t.Setenv("FLYBALL_FAKE_UV", "1")
	r := startRun(t, "name: t\n", "--uv", "--listen", "127.0.0.1:0")
	echoAt(t, r, r.addr(), nil)
	r.sigs <- syscall.SIGINT
	select {
	case err := <-r.done:
		r.done <- err
		if got, _ := os.ReadFile(r.marker); string(got) != "interrupt" {
			t.Fatalf("run = %v; the runner saw %q, want interrupt", err, got)
		}
	case <-time.After(8 * time.Second):
		t.Fatal("the runner kept running: the SIGINT never reached it")
	}
}

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

type syncBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}
