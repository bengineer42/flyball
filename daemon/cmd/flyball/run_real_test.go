package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/principal"
)

// realRunner puts this worktree's engine/.venv/bin (after `uv sync
// --all-extras`) first on PATH, or skips.
func realRunner(t *testing.T) {
	t.Helper()
	bin, err := filepath.Abs("../../../engine/.venv/bin")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(bin, "flyball-runner")); err != nil {
		t.Skipf("no flyball-runner in %s (cd engine && UV_FROZEN=1 uv sync --all-extras): %v", bin, err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
}

// getJSON GETs url through the front until it answers 200 (up to wait).
func getJSON(t *testing.T, r *fakeRun, url string, wait time.Duration, v any) {
	t.Helper()
	last := ""
	for end := time.Now().Add(wait); time.Now().Before(end); time.Sleep(200 * time.Millisecond) {
		resp, err := testClient.Get(url)
		if err != nil {
			last = err.Error()
			continue
		}
		err = json.NewDecoder(resp.Body).Decode(v)
		resp.Body.Close()
		last = resp.Status
		if resp.StatusCode == 200 && err == nil {
			return
		}
	}
	t.Fatalf("GET %s: no 200 (last: %s); output:\n%s", url, last, r.output())
}

// tcpListeners counts pid's listening TCP sockets (/proc, Linux).
func tcpListeners(t *testing.T, pid int) int {
	t.Helper()
	fds, err := os.ReadDir(fmt.Sprintf("/proc/%d/fd", pid))
	if err != nil {
		t.Skipf("no /proc: %v", err)
	}
	inodes := map[string]bool{}
	for _, fd := range fds {
		l, _ := os.Readlink(fmt.Sprintf("/proc/%d/fd/%s", pid, fd.Name()))
		if ino, ok := strings.CutPrefix(l, "socket:["); ok {
			inodes[strings.TrimSuffix(ino, "]")] = true
		}
	}
	n := 0
	for _, table := range []string{"/proc/net/tcp", "/proc/net/tcp6"} {
		b, _ := os.ReadFile(table)
		for _, line := range strings.Split(string(b), "\n")[1:] {
			f := strings.Fields(line)
			if len(f) > 9 && f[3] == "0A" && inodes[f[9]] { // 0A: LISTEN
				n++
			}
		}
	}
	return n
}

// runnerPid reads the pid from the front-dir's runner.lock.
func runnerPid(t *testing.T, dir string) int {
	t.Helper()
	b, _ := os.ReadFile(filepath.Join(dir, frontdir.Lock))
	f := strings.Fields(string(b))
	if len(f) < 2 || f[0] != "pid" {
		t.Fatalf("runner.lock = %q", b)
	}
	pid, _ := strconv.Atoi(f[1])
	return pid
}

// `flyball run` with the real flyball-runner (examples/simulated/oven.yaml):
// the runner is reachable only through the front, over its socket; a
// forged principal at the socket gets 401; kill -9 is followed by a
// respawn with a fresh key, and the front answers 200 again.
func TestRunRealRunner(t *testing.T) {
	realRunner(t)
	dir := fakeEnv(t)
	runnerCommand = "flyball-runner"
	src, err := os.ReadFile("../../../examples/simulated/oven.yaml")
	if err != nil {
		t.Fatal(err)
	}
	rig := filepath.Join(dir, "oven.yaml")
	os.WriteFile(rig, src, 0o644)
	r := &fakeRun{t: t, dir: dir, sigs: make(chan os.Signal, 2), done: make(chan error, 1), addrs: make(chan string, 4)}
	runOut = r
	r.listen()
	port := freePort(t)
	go func() { r.done <- run([]string{rig, "--listen", "127.0.0.1:0", "--port", port}, r.sigs) }()
	t.Cleanup(func() { r.stop() })
	addr := r.addr()

	var info struct {
		Endpoint string `json:"endpoint"`
	}
	getJSON(t, r, "http://"+addr+"/api/runner", 60*time.Second, &info)
	id, _ := frontdir.FrontID(rig)
	fd := filepath.Join(frontdir.Root(id), "run")
	if info.Endpoint != "unix:"+filepath.Join(fd, "sock") {
		t.Fatalf("the runner binds %q, want its socket in %s", info.Endpoint, fd)
	}
	if c, err := net.DialTimeout("tcp", "127.0.0.1:"+port, 300*time.Millisecond); err == nil {
		c.Close()
		t.Fatal("something listens on the runner's --port: it must bind only its socket")
	}
	if tcpListeners(t, os.Getpid()) == 0 {
		t.Fatal("tcpListeners does not see this process's own front: the check cannot bite")
	}
	if n := tcpListeners(t, runnerPid(t, fd)); n != 0 {
		t.Fatalf("the runner holds %d listening TCP sockets, want 0", n)
	}
	var auth map[string]any
	getJSON(t, r, "http://"+addr+"/api/auth", 5*time.Second, &auth)
	if auth["v"] != float64(2) || auth["shape"] != "local" {
		t.Fatalf("/api/auth = %v", auth)
	}

	// Forged principals, straight at the socket.
	ep := endpoint.Endpoint{Network: "unix", Address: filepath.Join(fd, "sock")}
	aud, _ := os.ReadFile(filepath.Join(fd, frontdir.Aud))
	var wrong principal.Key
	now := time.Now()
	forged, _ := principal.Mint(wrong, principal.Claims{Sub: "local:console", Sid: "x", Scp: []string{"operate", "read"},
		Kind: "human", Aud: strings.TrimSpace(string(aud)), Sch: "http", Iat: now.Unix(), Exp: now.Add(time.Minute).Unix()})
	direct := &http.Client{Transport: ep.Transport(), Timeout: 5 * time.Second}
	for name, tok := range map[string]string{"none": "", "wrong key": forged} {
		req, _ := http.NewRequest("GET", ep.URL("")+"/api/runner", nil)
		if tok != "" {
			req.Header.Set(principal.Header, tok)
		}
		resp, err := direct.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 401 {
			t.Fatalf("%s: the socket answered %d, want 401", name, resp.StatusCode)
		}
		t.Logf("%s principal at the socket: %d %s", name, resp.StatusCode, resp.Header.Get("X-Flyball-Principal-Error"))
	}
	// A forged principal sent to the front is dropped; the front's own goes on.
	req, _ := http.NewRequest("GET", "http://"+addr+"/api/runner", nil)
	req.Header.Set(principal.Header, forged)
	resp, err := testClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("through the front with a client-sent principal: %d, want 200 (the header dropped)", resp.StatusCode)
	}

	key1, _ := frontdir.ReadKey(fd)
	pid1 := runnerPid(t, fd)
	if err := syscall.Kill(pid1, syscall.SIGKILL); err != nil {
		t.Fatal(err)
	}
	t.Logf("kill -9 %d", pid1)
	start := time.Now()
	for {
		if time.Since(start) > 90*time.Second {
			t.Fatalf("no respawn; output:\n%s", r.output())
		}
		time.Sleep(200 * time.Millisecond)
		b, _ := os.ReadFile(filepath.Join(fd, frontdir.Lock))
		if strings.HasPrefix(string(b), "pid ") && !strings.HasPrefix(string(b), fmt.Sprintf("pid %d ", pid1)) {
			break
		}
	}
	getJSON(t, r, "http://"+addr+"/api/runner", 60*time.Second, &info)
	key2, _ := frontdir.ReadKey(fd)
	pid2 := runnerPid(t, fd)
	if key1 == key2 || pid1 == pid2 {
		t.Fatalf("respawn kept key or pid: pid %d -> %d", pid1, pid2)
	}
	t.Logf("respawned as %d with a fresh key; the front answered 200 after %s", pid2, time.Since(start).Round(time.Millisecond))

	if err := r.stop(); err != nil {
		t.Fatalf("run = %v", err)
	}
}
