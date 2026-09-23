//go:build unix

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front"
	"flyballd/internal/fronttest"
)

// daemonEnv, set to a flyballd.yaml, makes this test binary flyballd
// itself (daemonMain), so a test can signal a real flyballd process.
const daemonEnv = "FLYBALLD_TEST_DAEMON"

// daemonProc is flyballd as a process of its own, in its own process
// group (as a shell job would be), its stderr collected.
type daemonProc struct {
	*testDaemon
	cmd  *exec.Cmd
	done chan struct{}
}

var listeningRe = regexp.MustCompile(`flyballd listening on (\S+)`)

// spawnDaemon starts flyballd on cfg as a process; killed at the test's end
// if it is still there.
func spawnDaemon(t *testing.T, dir, cfg string) *daemonProc {
	t.Helper()
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(self)
	cmd.Env = append(os.Environ(), daemonEnv+"="+cfg)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	logs := &logBuffer{}
	addrs := make(chan string, 1)
	p := &daemonProc{cmd: cmd, done: make(chan struct{})}
	go func() {
		sc := bufio.NewScanner(stderr)
		for sc.Scan() {
			line := sc.Text()
			logs.Write([]byte(line + "\n"))
			if m := listeningRe.FindStringSubmatch(line); m != nil {
				select {
				case addrs <- m[1]:
				default:
				}
			}
		}
		cmd.Wait()
		close(p.done)
	}()
	t.Cleanup(func() {
		select {
		case <-p.done:
		default:
			cmd.Process.Kill()
			<-p.done
		}
	})
	select {
	case a := <-addrs:
		p.testDaemon = &testDaemon{t: t, addr: a, dataDir: filepath.Join(dir, "data"), rigDir: filepath.Join(dir, "rig"), logs: logs}
		return p
	case <-p.done:
		t.Fatalf("flyballd exited\n%s", logs)
	case <-time.After(15 * time.Second):
		t.Fatalf("flyballd never listened\n%s", logs)
	}
	return nil
}

// exit waits for flyballd to exit after a signal.
func (p *daemonProc) exit(t *testing.T) {
	t.Helper()
	select {
	case <-p.done:
	case <-time.After(20 * time.Second):
		t.Fatalf("flyballd did not exit\n%s", p.logs)
	}
}

// killRunners ends every runner still holding a runner.lock under dir's
// runtime dir: a test's teardown, since flyballd leaves them running.
func killRunners(t *testing.T, dir string) {
	locks, _ := filepath.Glob(filepath.Join(dir, "rt", "flyball", "*", "*", frontdir.Lock))
	for _, lock := range locks {
		fd := filepath.Dir(lock)
		if held, _ := frontdir.LockHeld(fd); !held {
			continue
		}
		pid := lockPid(lock)
		if pid <= 0 || pid == os.Getpid() {
			continue
		}
		syscall.Kill(pid, syscall.SIGTERM)
		for end := time.Now().Add(5 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
			if held, _ := frontdir.LockHeld(fd); !held {
				break
			}
		}
		if held, _ := frontdir.LockHeld(fd); held {
			syscall.Kill(pid, syscall.SIGKILL)
		}
	}
}

func lockPid(path string) int {
	b, _ := os.ReadFile(path)
	var pid int
	f := strings.Fields(string(b))
	if len(f) >= 2 && f[0] == "pid" {
		pid, _ = strconv.Atoi(f[1])
	}
	return pid
}

// alive: pid is a process that has not exited (a zombie counts as gone).
func alive(pid int) bool {
	if syscall.Kill(pid, 0) != nil {
		return false
	}
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return true
	}
	s := string(b)
	if i := strings.LastIndexByte(s, ')'); i >= 0 && i+2 < len(s) {
		return s[i+2] != 'Z'
	}
	return true
}

// ovenDir is the front-dir flyballd gives manifest oven under cfg.
func ovenDir(t *testing.T, dir, cfg string) string {
	t.Helper()
	id, err := frontdir.FrontID(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Join(dir, "rt", "flyball", id, "oven")
}

// answers: the runner in fd answers the signed handshake at its socket,
// directly, as the pid given.
func answers(t *testing.T, fd string, pid int) {
	t.Helper()
	key, err := frontdir.ReadKey(fd)
	if err != nil {
		t.Fatal(err)
	}
	ep := endpoint.Endpoint{Network: "unix", Address: filepath.Join(fd, frontdir.Sock)}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	info, err := endpoint.Handshake(ctx, ep, "/oven", "oven", key, front.ProbeSigner(nil))
	if err != nil {
		t.Fatalf("the runner at %s does not answer: %v", ep, err)
	}
	if info.Pid != pid {
		t.Fatalf("the runner at %s is pid %d, want %d", ep, info.Pid, pid)
	}
}

func echo(t *testing.T, d *testDaemon, ok func(fronttest.Echo) bool) fronttest.Echo {
	t.Helper()
	var e fronttest.Echo
	json.Unmarshal(d.until("/oven/api/echo", "", 20*time.Second, func(b []byte) bool {
		var e fronttest.Echo
		return json.Unmarshal(b, &e) == nil && (ok == nil || ok(e))
	}), &e)
	return e
}

// D-037: SIGTERM ends flyballd, not its runners -- the runner is still
// alive afterwards and still answers at its socket.
func TestSIGTERMLeavesTheRunnerRunning(t *testing.T) {
	fakeOnPath(t)
	dir, cfg := daemonFixture(t, "", "name: oven\n")
	t.Cleanup(func() { killRunners(t, dir) })
	d := spawnDaemon(t, dir, cfg)
	first := echo(t, d.testDaemon, nil)

	d.cmd.Process.Signal(syscall.SIGTERM)
	d.exit(t)
	time.Sleep(300 * time.Millisecond)
	if !alive(first.Pid) {
		t.Fatalf("runner %d died with flyballd\n%s", first.Pid, d.logs)
	}
	answers(t, ovenDir(t, dir, cfg), first.Pid)
}

// Ctrl-C in a terminal is SIGINT to the whole foreground process group:
// flyballd's runners are not in it, so they do not hear it.
func TestSIGINTToFlyballdsGroupLeavesTheRunnerRunning(t *testing.T) {
	fakeOnPath(t)
	dir, cfg := daemonFixture(t, "", "name: oven\n")
	t.Cleanup(func() { killRunners(t, dir) })
	d := spawnDaemon(t, dir, cfg)
	first := echo(t, d.testDaemon, nil)

	syscall.Kill(-d.cmd.Process.Pid, syscall.SIGINT)
	d.exit(t)
	time.Sleep(300 * time.Millisecond)
	if !alive(first.Pid) {
		t.Fatalf("runner %d died with flyballd's process group\n%s", first.Pid, d.logs)
	}
	answers(t, ovenDir(t, dir, cfg), first.Pid)
}

type runnerRow struct {
	Name    string `json:"name"`
	Status  string `json:"status"`
	Pid     int    `json:"pid"`
	Adopted bool   `json:"adopted"`
	Reason  string `json:"reason"`
}

// row waits for /api/runners to show oven as ok says.
func row(t *testing.T, d *testDaemon, manage string, ok func(runnerRow) bool) runnerRow {
	t.Helper()
	var got runnerRow
	d.until("/api/runners", manage, 20*time.Second, func(b []byte) bool {
		var rows []runnerRow
		if json.Unmarshal(b, &rows) != nil || len(rows) != 1 {
			return false
		}
		got = rows[0]
		return ok(got)
	})
	return got
}

// D-037: a flyballd that starts while its runner is still running adopts
// it -- the same process, the same key (control never interrupted),
// reachable through the front, shown adopted with its pid -- and, when
// that runner later dies, respawns it by its policy.
func TestARestartedFlyballdAdoptsItsRunner(t *testing.T) {
	fakeOnPath(t)
	dir, cfg := daemonFixture(t, "", "name: oven\n")
	t.Cleanup(func() { killRunners(t, dir) })
	d1 := spawnDaemon(t, dir, cfg)
	manage := d1.token("manage")
	first := echo(t, d1.testDaemon, nil)
	d1.cmd.Process.Signal(syscall.SIGTERM)
	d1.exit(t)

	d2 := spawnDaemon(t, dir, cfg)
	r := row(t, d2.testDaemon, manage, func(r runnerRow) bool { return r.Status == "running" })
	if !r.Adopted || r.Pid != first.Pid {
		t.Fatalf("/api/runners: %+v, want adopted, pid %d\n%s", r, first.Pid, d2.logs)
	}
	again := echo(t, d2.testDaemon, nil)
	if again.Pid != first.Pid || again.Key != first.Key {
		t.Fatalf("through the restarted front: pid %d key %s, want the same runner (pid %d key %s)", again.Pid, again.Key, first.Pid, first.Key)
	}

	syscall.Kill(first.Pid, syscall.SIGKILL)
	respawned := echo(t, d2.testDaemon, func(e fronttest.Echo) bool { return e.Pid != first.Pid })
	if respawned.Key == first.Key {
		t.Error("the respawn kept the adopted runner's key")
	}
	r = row(t, d2.testDaemon, manage, func(r runnerRow) bool { return r.Status == "running" && r.Pid == respawned.Pid })
	if r.Adopted {
		t.Errorf("the respawned runner shows adopted: %+v", r)
	}
}

// A runner.lock held by a runner flyballd cannot adopt -- one too old to
// enforce the principal, or one under another key -- leaves the rig busy
// with the reason; its key is never rewritten and the front does not
// route to it.
func TestAForeignRunnerLeavesTheRigBusy(t *testing.T) {
	for _, c := range []struct{ name, reason string }{{"old", "401"}, {"foreign", "401"}} {
		t.Run(c.name, func(t *testing.T) {
			fakeOnPath(t)
			dir, cfg := daemonFixture(t, "", "name: oven\n")
			fd := ovenDir(t, dir, cfg)
			if err := os.MkdirAll(filepath.Dir(fd), 0o700); err != nil {
				t.Fatal(err)
			}
			if err := frontdir.Prepare(fd); err != nil {
				t.Fatal(err)
			}
			ep := endpoint.Endpoint{Network: "unix", Address: filepath.Join(fd, frontdir.Sock)}
			if _, err := frontdir.Write(fd, "oven", ep); err != nil {
				t.Fatal(err)
			}
			key, _ := os.ReadFile(filepath.Join(fd, frontdir.Key))
			lock, err := os.OpenFile(filepath.Join(fd, frontdir.Lock), os.O_RDWR|os.O_CREATE, 0o600)
			if err != nil {
				t.Fatal(err)
			}
			defer lock.Close()
			syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
			lock.WriteString("pid " + strconv.Itoa(os.Getpid()) + " rig oven\n")
			if c.name == "old" {
				// Too old for a front: answers without a principal.
				ln, err := net.Listen("unix", ep.Address)
				if err != nil {
					t.Fatal(err)
				}
				srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Write([]byte(`{"protocol":1,"aud":"oven"}`))
				})}
				go srv.Serve(ln)
				defer srv.Close()
			} else {
				r, err := fronttest.Serve(ep, [32]byte{7, 7, 7}, "oven", "/oven")
				if err != nil {
					t.Fatal(err)
				}
				defer r.Close()
			}

			d := spawnDaemon(t, dir, cfg)
			manage := d.token("manage")
			r := row(t, d.testDaemon, manage, func(r runnerRow) bool { return r.Status == "busy" })
			if !strings.Contains(r.Reason, c.reason) || r.Adopted {
				t.Errorf("/api/runners: %+v, want busy naming %q", r, c.reason)
			}
			if code, body := d.get("/oven/api/echo", ""); code != 503 {
				t.Errorf("the front routed to a busy rig: %d %s", code, body)
			}
			if k, _ := os.ReadFile(filepath.Join(fd, frontdir.Key)); string(k) != string(key) {
				t.Error("the key of a live front-dir was rewritten")
			}
		})
	}
}

// The real flyball-runner (examples/simulated/oven.yaml) outlives a
// SIGTERM'd flyballd and is adopted by the next one: the same pid,
// reachable through the front.
func TestRealRunnerIsAdopted(t *testing.T) {
	bin, _ := filepath.Abs("../../../engine/.venv/bin")
	if _, err := os.Stat(filepath.Join(bin, "flyball-runner")); err != nil {
		t.Skipf("no flyball-runner in %s (cd engine && UV_FROZEN=1 uv sync --all-extras)", bin)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	rig, err := os.ReadFile("../../../examples/simulated/oven.yaml")
	if err != nil {
		t.Fatal(err)
	}
	dir, cfg := daemonFixture(t, "", string(rig))
	t.Cleanup(func() { killRunners(t, dir) })
	d1 := spawnDaemon(t, dir, cfg)
	manage := d1.token("manage")
	first := row(t, d1.testDaemon, manage, func(r runnerRow) bool { return r.Status == "running" && r.Pid != 0 })
	d1.until("/oven/api/runner", "", 30*time.Second, nil)
	d1.cmd.Process.Signal(syscall.SIGTERM)
	d1.exit(t)
	time.Sleep(500 * time.Millisecond)
	if !alive(first.Pid) {
		t.Fatalf("the runner %d died with flyballd", first.Pid)
	}

	d2 := spawnDaemon(t, dir, cfg)
	r := row(t, d2.testDaemon, manage, func(r runnerRow) bool { return r.Status == "running" })
	if !r.Adopted || r.Pid != first.Pid {
		t.Fatalf("/api/runners: %+v, want adopted, pid %d\n%s", r, first.Pid, d2.logs)
	}
	body := d2.until("/oven/api/runner", "", 10*time.Second, nil)
	t.Logf("adopted pid %d; /oven/api/runner through the new front: %.120s", r.Pid, body)
}

// buildCLI builds the flyball CLI (../flyball) into a temp dir.
func buildCLI(t *testing.T) string {
	t.Helper()
	bin := filepath.Join(t.TempDir(), "flyball")
	out, err := exec.Command(filepath.Join(runtime.GOROOT(), "bin", "go"), "build", "-o", bin, "../flyball").CombinedOutput()
	if err != nil {
		t.Fatalf("building the flyball CLI: %v\n%s", err, out)
	}
	return bin
}

// cli runs the flyball CLI against flyballd at addr; its exit code and
// output.
func cli(t *testing.T, bin, addr string, args ...string) (int, string) {
	t.Helper()
	cmd := exec.Command(bin, args...)
	cmd.Env = append(os.Environ(), "FLYBALLD_URL=http://"+addr, "FLYBALLD_TOKEN=", "FLYBALL_TOKEN=", "XDG_CONFIG_HOME="+t.TempDir())
	out, err := cmd.CombinedOutput()
	code := 0
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		code = ee.ExitCode()
	} else if err != nil {
		t.Fatal(err)
	}
	return code, string(out)
}

// D-037's two stop-alls, end to end with the real flyball-runner and the
// real CLI against a real flyballd: `flyball stop --all` stops every rig
// (operate) and reports each, leaving the runners up; `flyball runners
// stop --all` needs manage, and then ends every runner process.
func TestStopAllsAgainstRealRunners(t *testing.T) {
	bin, _ := filepath.Abs("../../../engine/.venv/bin")
	if _, err := os.Stat(filepath.Join(bin, "flyball-runner")); err != nil {
		t.Skipf("no flyball-runner in %s (cd engine && UV_FROZEN=1 uv sync --all-extras)", bin)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	rigs := map[string]string{}
	for _, name := range []string{"oven", "chiller"} {
		b, err := os.ReadFile("../../../examples/simulated/" + name + ".yaml")
		if err != nil {
			t.Fatal(err)
		}
		rigs[name] = string(b)
	}
	flyball := buildCLI(t)
	dir, cfg := daemonFixtureRigs(t, "", rigs)
	t.Cleanup(func() { killRunners(t, dir) })
	d := spawnDaemon(t, dir, cfg)
	manage, operate := d.token("manage"), d.token("operate")
	var rows []runnerRow
	d.until("/api/runners", manage, 60*time.Second, func(b []byte) bool {
		rows = nil
		json.Unmarshal(b, &rows)
		return len(rows) == 2 && rows[0].Status == "running" && rows[1].Status == "running" && rows[0].Pid != 0 && rows[1].Pid != 0
	})
	for _, name := range []string{"oven", "chiller"} {
		d.until("/"+name+"/api/runner", "", 30*time.Second, nil)
	}

	code, out := cli(t, flyball, d.addr, "--token", operate, "stop", "--all", "--reason", "D-037 test")
	t.Logf("flyball stop --all: exit %d\n%s", code, out)
	if code != 0 || !strings.Contains(out, "oven:") || !strings.Contains(out, "chiller:") || strings.Count(out, "\nsoftware stop: D-037 test") != 2 {
		t.Fatalf("flyball stop --all: exit %d, want 0 and both rigs' reports", code)
	}
	for _, r := range rows {
		if !alive(r.Pid) {
			t.Errorf("runner %s (pid %d) gone after the rig stop; it should stay up", r.Name, r.Pid)
		}
	}

	code, out = cli(t, flyball, d.addr, "--token", operate, "runners", "stop", "--all")
	t.Logf("flyball runners stop --all (operate): exit %d\n%s", code, out)
	if code == 0 || !strings.Contains(out, "manage") {
		t.Fatalf("runners stop --all with an operate token: exit %d, want a refusal naming manage", code)
	}
	for _, r := range rows {
		if !alive(r.Pid) {
			t.Fatalf("runner %s (pid %d) gone after a refused runners stop", r.Name, r.Pid)
		}
	}

	code, out = cli(t, flyball, d.addr, "--token", manage, "runners", "stop", "--all")
	t.Logf("flyball runners stop --all (manage): exit %d\n%s", code, out)
	if code != 0 {
		t.Fatalf("runners stop --all with manage: exit %d", code)
	}
	for _, r := range rows {
		if alive(r.Pid) {
			t.Errorf("runner %s (pid %d) still there after runners stop --all", r.Name, r.Pid)
		}
	}
}
