package backend

import (
	"context"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/endpoint"
)

// A runner that exits 0 on SIGTERM, as flyball-runner does once it
// handles the signal.
const cleanOnTerm = `trap 'exit 0' TERM; while :; do sleep 0.02; done`

// newTestBackend runs script under sh in place of flyball-runner.
func newTestBackend(t *testing.T, script string) *ProcessBackend {
	t.Helper()
	b, err := NewProcessBackend(t.TempDir(), 0)
	if err != nil {
		t.Fatal(err)
	}
	b.command = func(string, []string) *exec.Cmd { return exec.Command("sh", "-c", script) }
	b.minBackoff = 20 * time.Millisecond
	b.ready = func(endpoint.Endpoint, string, string, [32]byte) bool { return false }
	b.probeInterval = 10 * time.Millisecond
	t.Cleanup(func() {
		b.mu.Lock()
		names := make([]string, 0, len(b.runners))
		for name := range b.runners {
			names = append(names, name)
		}
		b.mu.Unlock()
		for _, name := range names {
			b.Stop(name)
		}
	})
	return b
}

func (b *ProcessBackend) pid(name string) int {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok || rp.cmd == nil || rp.cmd.Process == nil {
		return 0
	}
	return rp.cmd.Process.Pid
}

// eventually polls cond for up to 3 s.
func eventually(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func mustStart(t *testing.T, b *ProcessBackend, name string) {
	t.Helper()
	mustStartWith(t, b, name, "")
}

func mustStartWith(t *testing.T, b *ProcessBackend, name, restart string) {
	t.Helper()
	if _, err := b.Start(name, Spec{ServerConfig: "rig.yaml", Restart: restart}); err != nil {
		t.Fatal(err)
	}
}

func TestRestartRestartsARunnerThatExitsCleanlyOnSIGTERM(t *testing.T) {
	b := newTestBackend(t, cleanOnTerm)
	mustStart(t, b, "r")
	first := b.pid("r")
	time.Sleep(100 * time.Millisecond) // let the trap be installed

	if err := b.Restart("r"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "a new process after Restart", func() bool {
		p := b.pid("r")
		return p != 0 && p != first
	})
	if st, _ := b.Status("r"); st == StatusStopped {
		t.Errorf("status after Restart: %s", st)
	}
}

// Stop while a crashed runner waits out its backoff must end its life:
// no new process afterwards, and Stop itself succeeds. Run under -race:
// Stop and supervise() both touch the runner's current process.
func TestStopDuringBackoffLeavesNoRunner(t *testing.T) {
	dir := t.TempDir()
	b := newTestBackend(t, "")
	b.minBackoff = 150 * time.Millisecond
	var mu sync.Mutex
	spawned := 0
	b.command = func(string, []string) *exec.Cmd {
		mu.Lock()
		spawned++
		mu.Unlock()
		// The first incarnation crashes; any later one would stay up.
		cmd := exec.Command("sh", "-c", `if [ -e once ]; then sleep 3; else touch once; exit 1; fi`)
		cmd.Dir = dir
		return cmd
	}
	mustStart(t, b, "r")
	eventually(t, "the first crash", func() bool {
		st, _ := b.Status("r")
		return st == StatusRestarting
	})

	if err := b.Stop("r"); err != nil {
		t.Errorf("Stop during backoff: %v", err)
	}
	time.Sleep(4 * b.minBackoff)
	mu.Lock()
	defer mu.Unlock()
	if spawned != 1 {
		t.Errorf("%d processes spawned; a Stop during backoff should leave the one that crashed", spawned)
	}
}

// Stop waits for the runner to go, and kills one that ignores SIGTERM.
func TestStopKillsARunnerThatIgnoresSIGTERM(t *testing.T) {
	b := newTestBackend(t, `trap '' TERM; while :; do sleep 0.02; done`)
	b.stopTimeout = 200 * time.Millisecond
	mustStart(t, b, "r")
	pid := b.pid("r")
	time.Sleep(100 * time.Millisecond) // let the trap be installed

	if err := b.Stop("r"); err != nil {
		t.Fatal(err)
	}
	if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
		t.Errorf("runner %d still there after Stop returned (kill -0: %v)", pid, err)
	}
}

// Stop's SIGKILL reaches the runner's whole process group. Under
// `uv_project:` flyballd's child is uv, which forwards SIGTERM but cannot
// forward SIGKILL: a runner stuck in its shutdown must not outlive Stop
// as an orphan holding the rig's locks.
func TestStopKillsTheRunnersGroup(t *testing.T) {
	out := t.TempDir()
	runner := `trap '' TERM; echo $$ > ` + out + `/runner; while :; do sleep 0.02; done`
	uv := `sh -c "$0" & c=$!; trap 'kill -TERM $c' TERM; while kill -0 $c 2>/dev/null; do wait $c; done`
	b := newTestBackend(t, "")
	b.command = func(string, []string) *exec.Cmd { return exec.Command("sh", "-c", uv, runner) }
	b.stopTimeout = 200 * time.Millisecond
	mustStart(t, b, "r")
	var pid int
	eventually(t, "the runner under uv", func() bool {
		b, err := os.ReadFile(out + "/runner")
		if err != nil {
			return false
		}
		_, err = fmt.Sscan(string(b), &pid)
		return err == nil
	})
	t.Cleanup(func() { syscall.Kill(pid, syscall.SIGKILL) })
	time.Sleep(100 * time.Millisecond) // let the traps be installed

	if err := b.Stop("r"); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for processAlive(pid, "") && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if processAlive(pid, "") {
		t.Errorf("the runner (pid %d) under uv survived Stop", pid)
	}
}

// setGOOS sets the platform D-044's tcp rule is decided for, for one test.
func setGOOS(t *testing.T, goos string) {
	t.Helper()
	was := endpoint.GOOS
	endpoint.GOOS = goos
	t.Cleanup(func() { endpoint.GOOS = was })
}

// network: tcp is logged when the runner is started (flyballd's log and
// the runner's): its port is open to every local user. Windows only
// (D-044), taken here by setting GOOS.
func TestTCPIsLogged(t *testing.T) {
	setGOOS(t, "windows")
	var buf strings.Builder
	var mu sync.Mutex
	log.SetOutput(writerFunc(func(p []byte) (int, error) { mu.Lock(); defer mu.Unlock(); return buf.Write(p) }))
	t.Cleanup(func() { log.SetOutput(os.Stderr) })
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	if _, err := b.Start("r", Spec{ServerConfig: "rig.yaml", Network: "tcp", Port: 8123}); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	logged := buf.String()
	mu.Unlock()
	if !strings.Contains(logged, "network: tcp") || !strings.Contains(logged, "127.0.0.1:8123") {
		t.Errorf("flyballd's log: %q, want a line naming network: tcp and the port", logged)
	}
	if l := readFile(t, filepath.Join(b.logDir, "r.log")); !strings.Contains(l, "network: tcp") {
		t.Errorf("the runner's log: %q, want the tcp line", l)
	}
	buf.Reset()
	b.Start("u", Spec{ServerConfig: "rig.yaml"})
	mu.Lock()
	defer mu.Unlock()
	if strings.Contains(buf.String(), "network: tcp") {
		t.Errorf("a unix runner logged %q", buf.String())
	}
}

// A runtime dir too deep for a socket path under it gives a runner a temp
// front-dir, which the next flyballd cannot find: its runner will not be
// adopted. That is logged, naming the runner.
func TestATempFrontDirUnderARootIsLogged(t *testing.T) {
	var buf strings.Builder
	var mu sync.Mutex
	log.SetOutput(writerFunc(func(p []byte) (int, error) { mu.Lock(); defer mu.Unlock(); return buf.Write(p) }))
	t.Cleanup(func() { log.SetOutput(os.Stderr) })
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	deep := filepath.Join(t.TempDir(), strings.Repeat("d", 90))
	if err := os.MkdirAll(deep, 0o700); err != nil {
		t.Fatal(err)
	}
	b.SetFront(FrontOptions{Root: deep})
	mustStart(t, b, "r")
	if dir := frontDirOf(t, b, "r"); strings.HasPrefix(dir, deep) {
		t.Fatalf("front-dir %s under the too-deep root", dir)
	}
	mu.Lock()
	defer mu.Unlock()
	if !strings.Contains(buf.String(), "runner r") || !strings.Contains(buf.String(), "not be adopted") {
		t.Errorf("flyballd's log: %q, want a warning that runner r's front-dir is temporary", buf.String())
	}
}

type writerFunc func([]byte) (int, error)

func (f writerFunc) Write(p []byte) (int, error) { return f(p) }

// Restart kills a runner that ignores SIGTERM after Stop's timeout -- a
// read stuck in a driver must not make it wait for ever -- and a new
// process follows.
func TestRestartKillsARunnerThatIgnoresSIGTERM(t *testing.T) {
	b := newTestBackend(t, `trap '' TERM; while :; do sleep 0.02; done`)
	b.stopTimeout = 200 * time.Millisecond
	mustStart(t, b, "r")
	first := b.pid("r")
	time.Sleep(100 * time.Millisecond) // let the trap be installed

	asked := time.Now()
	if err := b.Restart("r"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "a new process after Restart", func() bool {
		p := b.pid("r")
		return p != 0 && p != first
	})
	if took := time.Since(asked); took > b.stopTimeout+time.Second {
		t.Errorf("new process %v after Restart; the timeout is %v", took, b.stopTimeout)
	}
	if err := syscall.Kill(first, 0); err != syscall.ESRCH {
		t.Errorf("runner %d still there after Restart (kill -0: %v)", first, err)
	}
}

func status(b *ProcessBackend, name string) Status {
	st, _ := b.Status(name)
	return st
}

// The status follows the process: starting until the runner answers,
// running, restarting while a crash's backoff runs, then back up.
func TestStatusFollowsACrash(t *testing.T) {
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	b.minBackoff = 300 * time.Millisecond
	var mu sync.Mutex
	answering := false
	b.ready = func(endpoint.Endpoint, string, string, [32]byte) bool { mu.Lock(); defer mu.Unlock(); return answering }
	mustStart(t, b, "r")

	time.Sleep(50 * time.Millisecond)
	if st := status(b, "r"); st != StatusStarting {
		t.Errorf("before the runner answers: %s, want starting", st)
	}
	mu.Lock()
	answering = true
	mu.Unlock()
	eventually(t, "running", func() bool { return status(b, "r") == StatusRunning })

	first := b.pid("r")
	syscall.Kill(first, syscall.SIGKILL)
	eventually(t, "restarting", func() bool { return status(b, "r") == StatusRestarting })
	eventually(t, "running again, a new process", func() bool {
		return status(b, "r") == StatusRunning && b.pid("r") != first
	})
}

// A runner that exits 0 without being asked is stopped, not running.
func TestACleanExitIsStopped(t *testing.T) {
	b := newTestBackend(t, `exit 0`)
	mustStart(t, b, "r")
	eventually(t, "stopped", func() bool { return status(b, "r") == StatusStopped })
}

// countingCommand runs script and counts how often it was started.
func countingCommand(b *ProcessBackend, script string) func() int {
	var mu sync.Mutex
	n := 0
	b.command = func(string, []string) *exec.Cmd {
		mu.Lock()
		n++
		mu.Unlock()
		return exec.Command("sh", "-c", script)
	}
	return func() int { mu.Lock(); defer mu.Unlock(); return n }
}

func TestRestartNeverLeavesACrashFailed(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `exit 1`)
	mustStartWith(t, b, "r", RestartNever)
	eventually(t, "failed", func() bool { return status(b, "r") == StatusFailed })
	time.Sleep(5 * b.minBackoff)
	if n := spawned(); n != 1 {
		t.Errorf("restart: never started it %d times", n)
	}
}

func TestRestartAlwaysRestartsACleanExit(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `exit 0`)
	mustStartWith(t, b, "r", RestartAlways)
	eventually(t, "a clean exit restarted", func() bool { return spawned() >= 3 })
}

func TestRestartOnFailureStopsAfterACleanExit(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `exit 0`)
	mustStartWith(t, b, "r", RestartOnFailure)
	eventually(t, "stopped", func() bool { return status(b, "r") == StatusStopped })
	time.Sleep(5 * b.minBackoff)
	if n := spawned(); n != 1 {
		t.Errorf("restart: on-failure started a cleanly exited runner %d times", n)
	}
}

// A runner that has stopped or failed comes back on Restart.
func TestRestartBringsBackAFailedRunner(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `sleep 0.1; exit 1`)
	mustStartWith(t, b, "r", RestartNever)
	eventually(t, "failed", func() bool { return status(b, "r") == StatusFailed })
	if err := b.Restart("r"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "started again", func() bool { return spawned() == 2 })
	eventually(t, "failed again", func() bool { return status(b, "r") == StatusFailed })
}

// log_max_size: past the cap the log moves to NAME.log.1 and starts again,
// so a chatty runner cannot fill the disk.
func TestTheCapturedLogIsCapped(t *testing.T) {
	b := newTestBackend(t, `while :; do echo 0123456789012345678901234567890123456789; sleep 0.002; done`)
	b.maxLogSize = 4000
	b.logCheckInterval = 20 * time.Millisecond
	mustStart(t, b, "r")
	log := filepath.Join(b.logDir, "r.log")
	eventually(t, "r.log.1", func() bool {
		_, err := os.Stat(log + ".1")
		return err == nil
	})
	for range 20 {
		time.Sleep(20 * time.Millisecond)
		if fi, err := os.Stat(log); err != nil || fi.Size() > 4*b.maxLogSize {
			t.Fatalf("r.log: %v, %v; the cap is %d", fi.Size(), err, b.maxLogSize)
		}
	}
	if fi, _ := os.Stat(log + ".1"); fi.Size() < b.maxLogSize {
		t.Errorf("r.log.1 is %d bytes, less than the cap it was rotated at", fi.Size())
	}
}

func mode(t *testing.T, path string) os.FileMode {
	t.Helper()
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return fi.Mode().Perm()
}

// Runner logs can hold tokens (a ?token= in an access log line): the
// directory is 0700 and every log file 0600, also ones left 0644 by an
// older flyballd.
func TestLogsAreOwnerOnly(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "logs")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "old.log"), []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	b, err := NewProcessBackend(dir, 0)
	if err != nil {
		t.Fatal(err)
	}
	b.command = func(string, []string) *exec.Cmd {
		return exec.Command("sh", "-c", `while :; do echo 0123456789012345678901234567890123456789; sleep 0.002; done`)
	}
	b.ready = func(endpoint.Endpoint, string, string, [32]byte) bool { return false }
	b.maxLogSize = 1000
	b.logCheckInterval = 20 * time.Millisecond
	t.Cleanup(func() { b.Stop("new"); b.Stop("old") })

	if m := mode(t, dir); m != 0o700 {
		t.Errorf("log dir %v, want 0700", m)
	}
	for _, name := range []string{"new", "old"} {
		if _, err := b.Start(name, Spec{ServerConfig: "rig.yaml"}); err != nil {
			t.Fatal(err)
		}
		if m := mode(t, filepath.Join(dir, name+".log")); m != 0o600 {
			t.Errorf("%s.log %v, want 0600", name, m)
		}
	}
	rotated := filepath.Join(dir, "new.log.1")
	eventually(t, "new.log.1", func() bool { _, err := os.Stat(rotated); return err == nil })
	if m := mode(t, rotated); m != 0o600 {
		t.Errorf("new.log.1 %v, want 0600", m)
	}
}

// Exit 2 is flyball-runner's "bad config": the rig file does not validate
// or the rig cannot be built. Restarting cannot help, so no policy does.
func TestABadConfigExitIsNotRestarted(t *testing.T) {
	for _, policy := range []string{RestartOnFailure, RestartAlways} {
		b := newTestBackend(t, "")
		spawned := countingCommand(b, `exit 2`)
		mustStartWith(t, b, "r", policy)
		eventually(t, "failed", func() bool { return status(b, "r") == StatusFailed })
		time.Sleep(5 * b.minBackoff)
		if n := spawned(); n != 1 {
			t.Errorf("restart: %s started a runner with a bad config %d times", policy, n)
		}
	}
}

// --- the front-dir, fronted spawn and the exit codes of the channel ---

func readFile(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// recorder runs a fake runner that writes its argv, env and front-dir key
// to out/<n>.{argv,env,key} for its n-th incarnation, then runs script.
// <n>.key is written last, and renamed into place: once it exists, all
// three are complete (a test that kills the runner waits for it).
func recorder(b *ProcessBackend, out, script string) func() int {
	var mu sync.Mutex
	n := 0
	b.command = func(_ string, args []string) *exec.Cmd {
		mu.Lock()
		n++
		i := n
		mu.Unlock()
		rec := fmt.Sprintf(`o=%s/%d; printf '%%s\n' "$@" > $o.argv; env > $o.env; cat "$3/key" > $o.key.tmp 2>/dev/null; mv $o.key.tmp $o.key; `, out, i)
		return exec.Command("sh", append([]string{"-c", rec + script, "sh"}, args...)...)
	}
	return func() int { mu.Lock(); defer mu.Unlock(); return n }
}

func frontDirOf(t *testing.T, b *ProcessBackend, name string) string {
	t.Helper()
	ch, err := b.Channel(name)
	if err != nil {
		t.Fatal(err)
	}
	return ch.Dir
}

func TestSpawnWritesFrontDir(t *testing.T) {
	b := newTestBackend(t, "")
	out := t.TempDir()
	recorder(b, out, `while :; do sleep 0.02; done`)
	if _, err := b.Start("r", Spec{ServerConfig: "rig.yaml", RootPath: "/r", Env: []string{"FLYBALL_TEST_EXTRA=kept"}}); err != nil {
		t.Fatal(err)
	}
	dir := frontDirOf(t, b, "r")
	eventually(t, "the runner's record", func() bool { _, err := os.Stat(out + "/1.key"); return err == nil })

	if m := mode(t, dir); m != 0o700 {
		t.Errorf("front-dir %v, want 0700", m)
	}
	for _, f := range []string{"key", "aud", "endpoint"} {
		if m := mode(t, filepath.Join(dir, f)); m != 0o600 {
			t.Errorf("%s %v, want 0600", f, m)
		}
	}
	key := strings.TrimSpace(readFile(t, filepath.Join(dir, "key")))
	if len(key) != 64 {
		t.Fatalf("key %q", key)
	}
	if a := readFile(t, filepath.Join(dir, "aud")); a != "r\n" {
		t.Errorf("aud %q, want the runner's name", a)
	}
	if e := readFile(t, filepath.Join(dir, "endpoint")); e != "unix:"+filepath.Join(dir, "sock")+"\n" {
		t.Errorf("endpoint %q", e)
	}
	argv := strings.Split(strings.TrimSpace(readFile(t, out+"/1.argv")), "\n")
	want := []string{"rig.yaml", "--front-dir", dir, "--root-path", "/r"}
	if strings.Join(argv, " ") != strings.Join(want, " ") {
		t.Errorf("argv %q, want %q", argv, want)
	}
	env := readFile(t, out+"/1.env")
	for what, s := range map[string]string{"argv": strings.Join(argv, " "), "env": env} {
		if strings.Contains(s, key) {
			t.Errorf("the key is in the runner's %s", what)
		}
	}
	if !strings.Contains(env, "FLYBALL_TEST_EXTRA=kept") {
		t.Error("Spec.Env did not reach the runner")
	}
	if got := readFile(t, out+"/1.key"); strings.TrimSpace(got) != key {
		t.Errorf("the runner read key %q from its front-dir, the front wrote %q", got, key)
	}
	if ch, _ := b.Channel("r"); fmt.Sprintf("%x", ch.Key) != key || ch.Aud != "r" || ch.Endpoint.Network != "unix" {
		t.Errorf("Channel: %+v", ch)
	}
}

// A kill -9'd runner is respawned with a fresh key, the same aud and the
// same argv and env -- supervise() rebuilds the whole command, not just
// Path and Args.
func TestRespawnFreshKey(t *testing.T) {
	b := newTestBackend(t, "")
	out := t.TempDir()
	spawned := recorder(b, out, `while :; do sleep 0.02; done`)
	if _, err := b.Start("r", Spec{ServerConfig: "rig.yaml", RootPath: "/r", Env: []string{"FLYBALL_TEST_EXTRA=kept"}}); err != nil {
		t.Fatal(err)
	}
	dir := frontDirOf(t, b, "r")
	eventually(t, "the first record", func() bool { _, err := os.Stat(out + "/1.key"); return err == nil })
	first := b.pid("r")
	firstKey := readFile(t, filepath.Join(dir, "key"))

	syscall.Kill(first, syscall.SIGKILL)
	eventually(t, "a respawn", func() bool { _, err := os.Stat(out + "/2.key"); return err == nil && spawned() == 2 })

	if k := readFile(t, filepath.Join(dir, "key")); k == firstKey {
		t.Error("the respawn kept the dead runner's key")
	}
	if k1, k2 := readFile(t, out+"/1.key"), readFile(t, out+"/2.key"); k1 == k2 || k1 != firstKey {
		t.Errorf("keys the runners read: %q then %q", k1, k2)
	}
	if a := readFile(t, filepath.Join(dir, "aud")); a != "r\n" {
		t.Errorf("aud after respawn %q", a)
	}
	if a1, a2 := readFile(t, out+"/1.argv"), readFile(t, out+"/2.argv"); a1 != a2 {
		t.Errorf("argv changed across a respawn: %q -> %q", a1, a2)
	}
	if !strings.Contains(readFile(t, out+"/2.env"), "FLYBALL_TEST_EXTRA=kept") {
		t.Error("the respawn lost Spec.Env")
	}
}

// holdLock flocks dir/runner.lock as a live runner would.
func holdLock(t *testing.T, dir string) (release func()) {
	t.Helper()
	f, err := os.OpenFile(filepath.Join(dir, "runner.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	return func() { syscall.Flock(int(f.Fd()), syscall.LOCK_UN); f.Close() }
}

func shortDir(t *testing.T) string {
	t.Helper()
	d, err := os.MkdirTemp("", "fb-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(d) })
	return d
}

// A front-dir whose runner.lock is held belongs to a live runner: its key
// is not rewritten and no second runner is spawned into it.
func TestLiveLockKeepsKey(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `while :; do sleep 0.02; done`)
	dir := filepath.Join(shortDir(t), "r")
	if err := os.Mkdir(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	live := strings.Repeat("ab", 32) + "\n"
	if err := os.WriteFile(filepath.Join(dir, "key"), []byte(live), 0o600); err != nil {
		t.Fatal(err)
	}
	release := holdLock(t, dir)

	if _, err := b.Start("r", Spec{ServerConfig: "rig.yaml", FrontDir: dir}); err != nil {
		t.Fatal(err)
	}
	// Starting while the front-dir's holder is looked at (adoption,
	// D-037), then busy: it names no pid and has no aud or endpoint.
	eventually(t, "busy", func() bool { return status(b, "r") == StatusBusy })
	time.Sleep(5 * b.minBackoff)
	if n := spawned(); n != 0 {
		t.Errorf("%d runners spawned into a live front-dir", n)
	}
	if k := readFile(t, filepath.Join(dir, "key")); k != live {
		t.Errorf("the live key was rewritten: %q", k)
	}
	if err := b.Restart("r"); err == nil {
		t.Error("Restart spawned into a live front-dir")
	}

	// The live runner goes; a Restart takes the dir over with a fresh key.
	release()
	if err := b.Restart("r"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "a spawn after the lock went", func() bool { return spawned() == 1 })
	if k := readFile(t, filepath.Join(dir, "key")); k == live {
		t.Error("the key was not rewritten once the lock was free")
	}
}

// A runner that was busy from the start (never spawned) stops at once.
func TestStopABusyRunner(t *testing.T) {
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	dir := filepath.Join(shortDir(t), "r")
	if err := os.Mkdir(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	defer holdLock(t, dir)()
	if _, err := b.Start("r", Spec{ServerConfig: "rig.yaml", FrontDir: dir}); err != nil {
		t.Fatal(err)
	}
	stopped := make(chan error, 1)
	go func() { stopped <- b.Stop("r") }()
	select {
	case err := <-stopped:
		if err != nil {
			t.Error(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Stop hung on a runner that was never spawned")
	}
}

// Exit 3 is RIG_BUSY: another runner has the rig. Restarting would only
// hit the same lock, so no policy does.
func TestExit3Busy(t *testing.T) {
	for _, policy := range []string{RestartOnFailure, RestartAlways} {
		b := newTestBackend(t, "")
		spawned := countingCommand(b, `exit 3`)
		mustStartWith(t, b, "r", policy)
		eventually(t, "busy", func() bool { return status(b, "r") == StatusBusy })
		time.Sleep(5 * b.minBackoff)
		if n := spawned(); n != 1 {
			t.Errorf("restart: %s started a busy runner %d times", policy, n)
		}
	}
}

// Exit 4 is FRONT_DIR: the runner found its front-dir unsafe or
// incomplete. The front rewrites it and respawns once, whatever the
// policy; a second exit 4 leaves it failed.
func TestExit4Respawn(t *testing.T) {
	for _, policy := range []string{RestartNever, RestartOnFailure} {
		b := newTestBackend(t, "")
		out := t.TempDir()
		spawned := recorder(b, out, `exit 4`)
		mustStartWith(t, b, "r", policy)
		eventually(t, "failed", func() bool { return status(b, "r") == StatusFailed })
		time.Sleep(5 * b.minBackoff)
		if n := spawned(); n != 2 {
			t.Errorf("restart: %s: %d spawns after exit 4, want 2", policy, n)
			continue
		}
		if k1, k2 := readFile(t, out+"/1.key"), readFile(t, out+"/2.key"); k1 == k2 || len(k2) != 65 {
			t.Errorf("restart: %s: the dir was not rewritten (%q, %q)", policy, k1, k2)
		}
	}
}

// A temp front-dir goes with its runner.
func TestStopRemovesATempFrontDir(t *testing.T) {
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	mustStart(t, b, "r")
	dir := frontDirOf(t, b, "r")
	if err := b.Stop("r"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Errorf("front-dir %s still there after Stop: %v", dir, err)
	}
}

// --- a real fronted runner: this test binary, re-executed ---

const helperEnv = "FLYBALLD_TEST_FAKE_RUNNER"

func TestMain(m *testing.M) {
	if os.Getenv(helperEnv) == "1" {
		fakeRunnerMain(os.Args[1:])
		return
	}
	os.Exit(m.Run())
}

// fakeSign stands in for principal.Mint.
func fakeSign(r *http.Request, key [32]byte, aud string) error {
	r.Header.Set("X-Flyball-Principal", fmt.Sprintf("%x/%s", key, aud))
	return nil
}

// fakeRunnerMain is a fronted runner: it reads key/aud/endpoint from
// --front-dir, exits 4 without them, holds runner.lock (exit 3 if it
// cannot), binds the endpoint, and answers GET
// <root>/api/auth/front as §WP0-8 says.
func fakeRunnerMain(args []string) {
	var dir, root string
	for i := 0; i+1 < len(args); i++ {
		switch args[i] {
		case "--front-dir":
			dir = args[i+1]
		case "--root-path":
			root = args[i+1]
		}
	}
	if dir == "" {
		os.Exit(4)
	}
	// runner.lock for its life, naming its pid, taken before the key is
	// read, as the real runner does.
	lock, err := os.OpenFile(filepath.Join(dir, "runner.lock"), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil || syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		os.Exit(3)
	}
	lock.Truncate(0)
	fmt.Fprintf(lock, "pid %d\n", os.Getpid())
	key, err1 := os.ReadFile(filepath.Join(dir, "key"))
	aud, err2 := os.ReadFile(filepath.Join(dir, "aud"))
	ep, err3 := os.ReadFile(filepath.Join(dir, "endpoint"))
	if err1 != nil || err2 != nil || err3 != nil {
		os.Exit(4)
	}
	e, err := endpoint.Parse(strings.TrimSpace(string(ep)))
	if err != nil {
		os.Exit(4)
	}
	want := strings.TrimSpace(string(key)) + "/" + strings.TrimSpace(string(aud))
	l, err := net.Listen(e.Network, e.Address)
	if err != nil {
		os.Exit(1)
	}
	http.Serve(l, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != root+"/api/auth/front" {
			http.NotFound(w, r)
			return
		}
		if r.Header.Get("X-Flyball-Principal") != want {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		fmt.Fprintf(w, `{"protocol":1,"aud":%q,"pid":%d,"flyball":"test"}`, strings.TrimSpace(string(aud)), os.Getpid())
	}))
}

func helperCommand(_ string, args []string) *exec.Cmd {
	cmd := exec.Command(os.Args[0], args...)
	cmd.Env = append(os.Environ(), helperEnv+"=1")
	return cmd
}

// A fronted runner on a real unix socket is running only once the
// handshake passes; kill -9 brings a respawn with a fresh key that the
// front's Channel follows, and the handshake passes again with it.
func TestFrontedRunnerOverUnix(t *testing.T) {
	b := newTestBackend(t, "")
	b.command = helperCommand
	b.ready = b.handshake
	b.SetFront(FrontOptions{Sign: fakeSign})
	if _, err := b.Start("oven", Spec{ServerConfig: "rig.yaml", RootPath: "/oven"}); err != nil {
		t.Fatal(err)
	}
	eventually(t, "running", func() bool { return status(b, "oven") == StatusRunning })
	ch, _ := b.Channel("oven")
	if _, err := endpoint.Handshake(context.Background(), ch.Endpoint, "/oven", "oven", ch.Key, fakeSign); err != nil {
		t.Fatalf("handshake with Channel's key: %v", err)
	}

	first := b.pid("oven")
	syscall.Kill(first, syscall.SIGKILL)
	eventually(t, "running again, a new process", func() bool {
		return status(b, "oven") == StatusRunning && b.pid("oven") != first
	})
	again, _ := b.Channel("oven")
	if again.Key == ch.Key {
		t.Error("Channel kept the dead runner's key")
	}
	if _, err := endpoint.Handshake(context.Background(), again.Endpoint, "/oven", "oven", ch.Key, fakeSign); err == nil {
		t.Error("the respawned runner accepted the old key")
	}
	if _, err := endpoint.Handshake(context.Background(), again.Endpoint, "/oven", "oven", again.Key, fakeSign); err != nil {
		t.Errorf("handshake after respawn: %v", err)
	}
}

// Without a signer the signed half of the handshake cannot be made, so a
// runner never shows running.
func TestNoSignerNeverRunning(t *testing.T) {
	b := newTestBackend(t, "")
	b.command = helperCommand
	b.ready = b.handshake
	if _, err := b.Start("oven", Spec{ServerConfig: "rig.yaml", RootPath: "/oven"}); err != nil {
		t.Fatal(err)
	}
	time.Sleep(300 * time.Millisecond)
	if st := status(b, "oven"); st != StatusStarting {
		t.Errorf("status %s with no signer, want starting", st)
	}
}

// network: tcp puts the endpoint on loopback Host:Port, on Windows
// (D-044), taken here by setting GOOS.
func TestTCPEndpoint(t *testing.T) {
	setGOOS(t, "windows")
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	ep, err := b.Start("r", Spec{ServerConfig: "rig.yaml", Network: "tcp", Port: 8123})
	if err != nil {
		t.Fatal(err)
	}
	if ep != "tcp:127.0.0.1:8123" {
		t.Errorf("Start returned %q", ep)
	}
	dir := frontDirOf(t, b, "r")
	if e := readFile(t, filepath.Join(dir, "endpoint")); e != "tcp:127.0.0.1:8123\n" {
		t.Errorf("endpoint file %q", e)
	}
	if _, err := b.Start("s", Spec{ServerConfig: "rig.yaml", Network: "tcp"}); err == nil {
		t.Error("tcp with no port started")
	}
}

// Off Windows network: tcp is refused (D-044) without refusing the rig
// (D-028): the runner starts on the unix socket in its front-dir, port or
// no port, and flyballd's log and the runner's say why.
func TestTCPOffWindowsRunsOnUnix(t *testing.T) {
	setGOOS(t, "linux")
	var buf strings.Builder
	var mu sync.Mutex
	log.SetOutput(writerFunc(func(p []byte) (int, error) { mu.Lock(); defer mu.Unlock(); return buf.Write(p) }))
	t.Cleanup(func() { log.SetOutput(os.Stderr) })
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	for name, port := range map[string]int{"r": 8123, "s": 0} {
		ep, err := b.Start(name, Spec{ServerConfig: "rig.yaml", Network: "tcp", Port: port})
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		dir := frontDirOf(t, b, name)
		want := "unix:" + filepath.Join(dir, "sock")
		if ep != want {
			t.Errorf("%s: Start returned %q, want %q", name, ep, want)
		}
		if e := readFile(t, filepath.Join(dir, "endpoint")); e != want+"\n" {
			t.Errorf("%s: endpoint file %q", name, e)
		}
		if l := readFile(t, filepath.Join(b.logDir, name+".log")); !strings.Contains(l, "D-044") {
			t.Errorf("%s: the runner's log: %q, want the D-044 line", name, l)
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if logged := buf.String(); !strings.Contains(logged, "D-044") || !strings.Contains(logged, "unix socket") {
		t.Errorf("flyballd's log: %q, want a line naming D-044 and the unix socket", logged)
	}
}

// Detach (flyballd exiting, D-037) leaves the runner running and no
// longer respawns it.
func TestDetachLeavesTheRunnerAndRespawnsNothing(t *testing.T) {
	b := newTestBackend(t, "")
	spawned := countingCommand(b, `while :; do sleep 0.02; done`)
	mustStart(t, b, "r")
	pid := b.pid("r")
	b.Detach()
	time.Sleep(50 * time.Millisecond)
	if err := syscall.Kill(pid, 0); err != nil {
		t.Fatalf("runner %d gone after Detach: %v", pid, err)
	}
	syscall.Kill(pid, syscall.SIGKILL)
	time.Sleep(10 * b.minBackoff)
	if n := spawned(); n != 1 {
		t.Errorf("%d spawns: a detached backend respawned its runner", n)
	}
}

// Every runner has a process group of its own, so a signal to flyballd's
// group (Ctrl-C in its terminal) does not reach it.
func TestARunnerHasItsOwnProcessGroup(t *testing.T) {
	b := newTestBackend(t, `while :; do sleep 0.02; done`)
	mustStart(t, b, "r")
	pid := b.pid("r")
	pgid, err := syscall.Getpgid(pid)
	if err != nil {
		t.Fatal(err)
	}
	if pgid != pid || pgid == syscall.Getpgrp() {
		t.Errorf("runner %d in process group %d (flyballd's is %d); want its own", pid, pgid, syscall.Getpgrp())
	}
}
