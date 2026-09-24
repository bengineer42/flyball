//go:build unix

package backend

import (
	"fmt"
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
	"flyballd/internal/endpoint/frontdir"
)

// frontedBackend is a backend whose runners are this test binary
// (fakeRunnerMain, holding runner.lock) under root, with the real
// handshake; spawned() counts the runners it started.
func frontedBackend(t *testing.T, root string) (*ProcessBackend, func() int) {
	t.Helper()
	b := newTestBackend(t, "")
	b.ready = b.handshake
	b.adoptPoll = 20 * time.Millisecond
	b.adoptWindow = 300 * time.Millisecond
	b.SetFront(FrontOptions{Root: root, Sign: fakeSign})
	var mu sync.Mutex
	n := 0
	b.command = func(uv string, args []string) *exec.Cmd {
		mu.Lock()
		n++
		mu.Unlock()
		return helperCommand(uv, args)
	}
	return b, func() int { mu.Lock(); defer mu.Unlock(); return n }
}

func detail(t *testing.T, b *ProcessBackend, name string) Detail {
	t.Helper()
	d, err := b.Detail(name)
	if err != nil {
		t.Fatal(err)
	}
	return d
}

// running starts oven on b and waits for it to pass the handshake.
func running(t *testing.T, b *ProcessBackend, spec Spec) Detail {
	t.Helper()
	if spec.ServerConfig == "" {
		spec.ServerConfig = "rig.yaml"
	}
	if spec.RootPath == "" {
		spec.RootPath = "/oven"
	}
	if _, err := b.Start("oven", spec); err != nil {
		t.Fatal(err)
	}
	eventually(t, "oven running", func() bool { return status(b, "oven") == StatusRunning })
	return detail(t, b, "oven")
}

// firstRunner is a flyballd that started oven and has since exited
// (Detach), leaving it running; it returns the runner's pid and key.
func firstRunner(t *testing.T, root string) (pid int, key [32]byte) {
	t.Helper()
	b1, _ := frontedBackend(t, root)
	d := running(t, b1, Spec{})
	ch, _ := b1.Channel("oven")
	b1.Detach()
	return d.Pid, ch.Key
}

// A flyballd that starts while its runner is still alive adopts it: no
// spawn, the same process and key, the handshake passes, the key file is
// untouched, and the status says running, adopted, with the lock's pid.
func TestAdoptsALiveRunner(t *testing.T) {
	root := shortDir(t)
	pid, key := firstRunner(t, root)
	keyFile := readFile(t, filepath.Join(root, "oven", "key"))

	b2, spawned := frontedBackend(t, root)
	d := running(t, b2, Spec{})
	if !d.Adopted || d.Pid != pid {
		t.Fatalf("detail %+v, want adopted, pid %d", d, pid)
	}
	if n := spawned(); n != 0 {
		t.Fatalf("%d runners spawned; adoption spawns none", n)
	}
	ch, _ := b2.Channel("oven")
	if ch.Key != key {
		t.Error("Channel's key is not the adopted runner's")
	}
	if k := readFile(t, filepath.Join(root, "oven", "key")); k != keyFile {
		t.Error("adoption rewrote the key")
	}
	info, err := endpoint.Handshake(t.Context(), ch.Endpoint, "/oven", "oven", ch.Key, fakeSign)
	if err != nil || info.Pid != pid {
		t.Errorf("handshake with the adopted channel: %+v, %v", info, err)
	}
}

// An adopted runner that dies is respawned by its manifest's policy: a new
// process with a fresh key. Its exit status cannot be known (flyballd is
// not its parent), so it counts as a crash.
func TestAnAdoptedRunnerThatDiesIsRespawned(t *testing.T) {
	root := shortDir(t)
	pid, key := firstRunner(t, root)
	b2, spawned := frontedBackend(t, root)
	running(t, b2, Spec{})

	syscall.Kill(pid, syscall.SIGKILL)
	eventually(t, "a respawn, running", func() bool {
		d, _ := b2.Detail("oven")
		return spawned() == 1 && d.Status == StatusRunning && d.Pid != pid && d.Pid != 0
	})
	d := detail(t, b2, "oven")
	if d.Adopted {
		t.Error("the respawned runner is still marked adopted")
	}
	if ch, _ := b2.Channel("oven"); ch.Key == key {
		t.Error("the respawn kept the adopted runner's key")
	}
}

// restart: never leaves a dead adopted runner failed, not respawned.
func TestAnAdoptedRunnerUnderRestartNever(t *testing.T) {
	root := shortDir(t)
	pid, _ := firstRunner(t, root)
	b2, spawned := frontedBackend(t, root)
	running(t, b2, Spec{Restart: RestartNever})

	syscall.Kill(pid, syscall.SIGKILL)
	eventually(t, "failed", func() bool { return status(b2, "oven") == StatusFailed })
	time.Sleep(5 * b2.minBackoff)
	if n := spawned(); n != 0 {
		t.Errorf("restart: never respawned an adopted runner %d times", n)
	}
}

// Stop ends an adopted runner through its pid, and Restart replaces it.
func TestStopAndRestartAnAdoptedRunner(t *testing.T) {
	root := shortDir(t)
	pid, _ := firstRunner(t, root)
	b2, spawned := frontedBackend(t, root)
	running(t, b2, Spec{})
	if err := b2.Restart("oven"); err != nil {
		t.Fatal(err)
	}
	eventually(t, "a new runner after Restart", func() bool {
		d, _ := b2.Detail("oven")
		return spawned() == 1 && d.Status == StatusRunning && d.Pid != pid
	})
	if alive(pid) {
		t.Errorf("adopted runner %d still there after Restart", pid)
	}

	root2 := shortDir(t)
	pid2, _ := firstRunner(t, root2)
	b3, _ := frontedBackend(t, root2)
	running(t, b3, Spec{})
	if err := b3.Stop("oven"); err != nil {
		t.Fatal(err)
	}
	if alive(pid2) {
		t.Errorf("adopted runner %d still there after Stop", pid2)
	}
	if held, _ := frontdir.LockHeld(filepath.Join(root2, "oven")); held {
		t.Error("runner.lock still held after Stop")
	}
}

// liveDir is a front-dir oven under root as a runner flyballd did not
// start would leave it: files written for aud, the lock held by this
// process with its pid, and serve (if not nil) on its socket.
func liveDir(t *testing.T, root, aud string, serve http.HandlerFunc) (dir, key string) {
	t.Helper()
	dir = filepath.Join(root, "oven")
	if err := frontdir.Prepare(dir); err != nil {
		t.Fatal(err)
	}
	ep := endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, frontdir.Sock)}
	if _, err := frontdir.Write(dir, aud, ep); err != nil {
		t.Fatal(err)
	}
	release := holdLock(t, dir)
	t.Cleanup(release)
	os.WriteFile(filepath.Join(dir, frontdir.Lock), []byte(fmt.Sprintf("pid %d rig oven\n", os.Getpid())), 0o600)
	if serve != nil {
		ln, err := net.Listen("unix", ep.Address)
		if err != nil {
			t.Fatal(err)
		}
		srv := &http.Server{Handler: serve}
		go srv.Serve(ln)
		t.Cleanup(func() { srv.Close() })
	}
	return dir, readFile(t, filepath.Join(dir, frontdir.Key))
}

// A lock held by a runner that is not ours to adopt leaves the rig busy,
// with the reason, its key never rewritten and nothing spawned.
func TestAHeldLockThatFailsTheHandshakeIsBusy(t *testing.T) {
	old := func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, `{"protocol":1,"aud":"oven"}`) }
	foreign := func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusUnauthorized) }
	for _, c := range []struct {
		name, aud string
		serve     http.HandlerFunc
		reason    string
	}{
		{"an old runner (answers unsigned)", "oven", old, "401"},
		{"a runner under another key", "oven", foreign, "401"},
		{"a runner for another aud", "stove", foreign, "aud"},
		{"nothing listening", "oven", nil, "not answering"},
	} {
		t.Run(c.name, func(t *testing.T) {
			root := shortDir(t)
			dir, key := liveDir(t, root, c.aud, c.serve)
			b, spawned := frontedBackend(t, root)
			if _, err := b.Start("oven", Spec{ServerConfig: "rig.yaml", RootPath: "/oven"}); err != nil {
				t.Fatal(err)
			}
			eventually(t, "busy", func() bool { return status(b, "oven") == StatusBusy })
			d := detail(t, b, "oven")
			if !strings.Contains(d.Reason, c.reason) || d.Adopted {
				t.Errorf("detail %+v, want a reason naming %q, not adopted", d, c.reason)
			}
			time.Sleep(5 * b.minBackoff)
			if n := spawned(); n != 0 {
				t.Errorf("%d runners spawned into a live front-dir", n)
			}
			if k := readFile(t, filepath.Join(dir, frontdir.Key)); k != key {
				t.Error("the key of a live front-dir was rewritten")
			}
			if ch, _ := b.Channel("oven"); ch.Key != ([32]byte{}) {
				t.Error("a busy runner's channel carries a key")
			}
		})
	}
}

// A front-dir that fails frontdir.Check (here: mode 0755) is never
// adopted, whoever holds its lock.
func TestAnUnsafeFrontDirIsNeverAdopted(t *testing.T) {
	root := shortDir(t)
	firstRunner(t, root)
	os.Chmod(filepath.Join(root, "oven"), 0o755)
	b2, spawned := frontedBackend(t, root)
	if _, err := b2.Start("oven", Spec{ServerConfig: "rig.yaml", RootPath: "/oven"}); err == nil {
		eventually(t, "not running", func() bool { st := status(b2, "oven"); return st == StatusBusy || st == StatusFailed })
		if d := detail(t, b2, "oven"); d.Adopted {
			t.Errorf("adopted a runner in an unsafe front-dir: %+v", d)
		}
	}
	if n := spawned(); n != 0 {
		t.Errorf("%d runners spawned", n)
	}
	os.Chmod(filepath.Join(root, "oven"), 0o700) // for the teardown
}

// alive: pid has not exited (a zombie counts as gone).
func alive(pid int) bool { return processAlive(pid, "") }

// A spawn that exits 3 because a runner took the front-dir's runner.lock
// in the meantime -- the runner of a flyballd that exited while it was
// starting (it takes runner.lock before it reads its key, so it holds the
// key just written) -- is that runner's front-dir, not a busy rig: it is
// adopted.
func TestExit3WithTheFrontDirHeldAdopts(t *testing.T) {
	root := shortDir(t)
	b, _ := frontedBackend(t, root)
	var live *exec.Cmd
	b.command = func(uv string, args []string) *exec.Cmd {
		if live != nil {
			return helperCommand(uv, args)
		}
		dir := args[2] // rig.yaml --front-dir DIR ...
		live = helperCommand(uv, args)
		if err := live.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { live.Process.Kill(); live.Wait() })
		eventually(t, "the other runner holds runner.lock", func() bool { h, _ := frontdir.LockHeld(dir); return h })
		return exec.Command("sh", "-c", "exit 3")
	}
	d := running(t, b, Spec{})
	if !d.Adopted || d.Pid != live.Process.Pid {
		t.Fatalf("detail %+v, want adopted, pid %d", d, live.Process.Pid)
	}
}

// liveRunner starts the fake runner in root/oven, as a flyballd that has
// since exited left it, with env (its FLYBALLD_TEST_* knobs), and waits
// until it holds runner.lock.
func liveRunner(t *testing.T, root string, env ...string) *exec.Cmd {
	t.Helper()
	dir := filepath.Join(root, "oven")
	if err := frontdir.Prepare(dir); err != nil {
		t.Fatal(err)
	}
	if _, err := frontdir.Write(dir, "oven", endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, frontdir.Sock)}); err != nil {
		t.Fatal(err)
	}
	cmd := helperCommand("", []string{"rig.yaml", "--front-dir", dir, "--root-path", "/oven"})
	cmd.Env = append(cmd.Env, env...)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cmd.Process.Kill(); cmd.Wait() })
	eventually(t, "the runner holds runner.lock", func() bool { h, _ := frontdir.LockHeld(dir); return h })
	return cmd
}

// adoptedWithin waits up to wait for oven to run adopted, as pid.
func adoptedWithin(t *testing.T, b *ProcessBackend, pid int, wait time.Duration) {
	t.Helper()
	if _, err := b.Start("oven", Spec{ServerConfig: "rig.yaml", RootPath: "/oven"}); err != nil {
		t.Fatal(err)
	}
	for end := time.Now().Add(wait); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if status(b, "oven") == StatusRunning {
			break
		}
	}
	if d := detail(t, b, "oven"); d.Status != StatusRunning || !d.Adopted || d.Pid != pid {
		t.Fatalf("detail %+v, want running, adopted, pid %d", d, pid)
	}
}

// A runner that holds runner.lock but has not yet written its pid there
// (it takes the lock first; a front's Write leaves the file empty) is
// starting: waited out within the adopt window, then adopted -- not left
// busy for good.
func TestAdoptsARunnerThatHasNotNamedItselfYet(t *testing.T) {
	root := shortDir(t)
	live := liveRunner(t, root, "FLYBALLD_TEST_NAME_DELAY=300ms")
	b, spawned := frontedBackend(t, root)
	b.adoptWindow = 5 * time.Second
	adoptedWithin(t, b, live.Process.Pid, 5*time.Second)
	if n := spawned(); n != 0 {
		t.Errorf("%d runners spawned; adoption spawns none", n)
	}
}

// A runner listening but slow to answer the handshake -- a probe that
// times out, or a connection closed before its answer (a Pi starting,
// ~20 s; ~37 s under load) -- is starting, not "not ours": retried
// within the adopt window, then adopted. Only an answer that proves
// otherwise leaves it busy (TestAHeldLockThatFailsTheHandshakeIsBusy).
func TestAdoptsARunnerSlowToAnswer(t *testing.T) {
	for _, c := range []struct{ name, env string }{
		{"connection closed unanswered", "FLYBALLD_TEST_DROP_FIRST=2"},
		{"probe timed out", "FLYBALLD_TEST_SLOW_FIRST=" + (endpoint.ProbeTimeout + 500*time.Millisecond).String()},
	} {
		t.Run(c.name, func(t *testing.T) {
			root := shortDir(t)
			live := liveRunner(t, root, c.env)
			b, spawned := frontedBackend(t, root)
			b.adoptWindow = 15 * time.Second
			adoptedWithin(t, b, live.Process.Pid, 15*time.Second)
			if n := spawned(); n != 0 {
				t.Errorf("%d runners spawned; adoption spawns none", n)
			}
		})
	}
}
