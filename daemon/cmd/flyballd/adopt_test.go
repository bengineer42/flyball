//go:build unix

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
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
