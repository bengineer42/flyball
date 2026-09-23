//go:build unix

package frontdir

import (
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"testing"

	"flyballd/internal/endpoint"
)

func perm(t *testing.T, path string) os.FileMode {
	t.Helper()
	fi, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	return fi.Mode().Perm()
}

func read(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// shortTemp is a temp dir short enough for a socket path.
func shortTemp(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "fb-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	return dir
}

func sockOf(t *testing.T, dir string) endpoint.Endpoint {
	t.Helper()
	e, err := endpoint.Parse("unix:" + filepath.Join(dir, Sock))
	if err != nil {
		t.Fatal(err)
	}
	return e
}

var keyLine = regexp.MustCompile(`^[0-9a-f]{64}\n$`)

func TestWriteFrontDir(t *testing.T) {
	dir := filepath.Join(shortTemp(t), "oven")
	if err := Prepare(dir); err != nil {
		t.Fatal(err)
	}
	if m := perm(t, dir); m != 0o700 {
		t.Errorf("front-dir %v, want 0700", m)
	}
	ep := sockOf(t, dir)
	key, err := Write(dir, "oven", ep)
	if err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{Key, Aud, EndpointFile} {
		if m := perm(t, filepath.Join(dir, f)); m != 0o600 {
			t.Errorf("%s %v, want 0600", f, m)
		}
	}
	k := read(t, filepath.Join(dir, Key))
	if !keyLine.MatchString(k) {
		t.Errorf("key file %q: want 64 lower-case hex and a newline", k)
	}
	if got, err := ReadKey(dir); err != nil || got != key {
		t.Errorf("ReadKey: %x, %v; Write returned %x", got, err, key)
	}
	if a := read(t, filepath.Join(dir, Aud)); a != "oven\n" {
		t.Errorf("aud %q", a)
	}
	if e := read(t, filepath.Join(dir, EndpointFile)); e != ep.String()+"\n" {
		t.Errorf("endpoint %q, want %q", e, ep.String()+"\n")
	}
	if key == ([32]byte{}) {
		t.Error("an all-zero key")
	}
	again, err := Write(dir, "oven", ep)
	if err != nil {
		t.Fatal(err)
	}
	if again == key {
		t.Error("a second Write kept the key; every spawn gets a fresh one")
	}
	left, _ := os.ReadDir(dir)
	if len(left) != 3 {
		t.Errorf("front-dir holds %d entries after two writes, want key/aud/endpoint only: %v", len(left), left)
	}
}

// A stale socket from a dead incarnation is removed, so the next probe
// sees ENOENT until the new runner binds.
func TestWriteRemovesAStaleSocket(t *testing.T) {
	dir := filepath.Join(shortTemp(t), "oven")
	if err := Prepare(dir); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, Sock), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Write(dir, "oven", sockOf(t, dir)); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(filepath.Join(dir, Sock)); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("stale sock still there: %v", err)
	}
}

func TestFrontDirSafety(t *testing.T) {
	base := shortTemp(t)

	real := filepath.Join(base, "real")
	if err := os.Mkdir(real, 0o700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(base, "link")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	wide := filepath.Join(base, "wide")
	if err := os.Mkdir(wide, 0o700); err != nil {
		t.Fatal(err)
	}
	os.Chmod(wide, 0o755)
	file := filepath.Join(base, "file")
	os.WriteFile(file, nil, 0o600)

	for name, dir := range map[string]string{"a symlink": link, "mode 0755": wide, "a regular file": file} {
		if err := Check(dir); err == nil {
			t.Errorf("Check accepted %s", name)
		}
		if err := Prepare(dir); err == nil {
			t.Errorf("Prepare accepted %s", name)
		}
		if _, err := Write(dir, "oven", sockOf(t, base)); err == nil {
			t.Errorf("Write accepted %s", name)
		}
	}
	if _, err := os.Lstat(filepath.Join(real, Key)); !errors.Is(err, os.ErrNotExist) {
		t.Error("a key was written through the symlink")
	}
	if err := Check(real); err != nil {
		t.Errorf("a 0700 dir of our own: %v", err)
	}

	// Another owner: pretend to be a different uid.
	was := euid
	euid = func() int { return was() + 1 }
	defer func() { euid = was }()
	if err := Check(real); err == nil || !strings.Contains(err.Error(), "owned") {
		t.Errorf("Check accepted a dir owned by another uid (%v)", err)
	}
	if _, err := Write(real, "oven", sockOf(t, real)); err == nil {
		t.Error("Write accepted a dir owned by another uid")
	}
}

func TestLiveLockRefusesWrite(t *testing.T) {
	dir := filepath.Join(shortTemp(t), "oven")
	if err := Prepare(dir); err != nil {
		t.Fatal(err)
	}
	ep := sockOf(t, dir)
	key, err := Write(dir, "oven", ep)
	if err != nil {
		t.Fatal(err)
	}
	if held, err := LockHeld(dir); err != nil || held {
		t.Fatalf("no runner.lock: held %v, %v", held, err)
	}
	// A runner holds runner.lock: another open file description, as a
	// separate process's would be.
	f, err := os.OpenFile(filepath.Join(dir, Lock), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	if held, err := LockHeld(dir); err != nil || !held {
		t.Fatalf("runner.lock held: LockHeld %v, %v", held, err)
	}
	if _, err := Write(dir, "oven", ep); !errors.Is(err, ErrLive) {
		t.Errorf("Write with runner.lock held: %v, want ErrLive", err)
	}
	if got, _ := ReadKey(dir); got != key {
		t.Error("the live key was rewritten")
	}
	syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	if held, _ := LockHeld(dir); held {
		t.Error("held after unlock")
	}
	// An unheld runner.lock left by a dead runner does not block.
	if _, err := Write(dir, "oven", ep); err != nil {
		t.Errorf("Write after the runner let go: %v", err)
	}
}

func TestFrontID(t *testing.T) {
	a, err := FrontID("/etc/flyball/rig.yaml")
	if err != nil {
		t.Fatal(err)
	}
	// sha256("/etc/flyball/rig.yaml")[:4], hex
	if !regexp.MustCompile(`^[0-9a-f]{8}$`).MatchString(a) {
		t.Errorf("FrontID %q", a)
	}
	cwd, _ := os.Getwd()
	rel, _ := FrontID("rig.yaml")
	abs, _ := FrontID(filepath.Join(cwd, "rig.yaml"))
	if rel != abs {
		t.Errorf("relative %s and absolute %s differ: the id is of the absolute path", rel, abs)
	}
}

func TestDirLocation(t *testing.T) {
	run := shortTemp(t)
	os.Chmod(run, 0o700)

	t.Setenv("RUNTIME_DIRECTORY", "")
	t.Setenv("XDG_RUNTIME_DIR", run)
	root := Root("1a2b3c4d")
	if root != filepath.Join(run, "flyball", "1a2b3c4d") {
		t.Errorf("Root under XDG_RUNTIME_DIR: %q", root)
	}
	dir, err := Dir(root, "oven")
	if err != nil {
		t.Fatal(err)
	}
	if dir != filepath.Join(root, "oven") {
		t.Errorf("Dir: %q", dir)
	}
	if m := perm(t, filepath.Join(run, "flyball")); m != 0o700 {
		t.Errorf("%s/flyball %v, want 0700", run, m)
	}
	if err := Check(dir); err != nil {
		t.Errorf("Dir made %s: %v", dir, err)
	}

	// systemd's RuntimeDirectory= wins: /run/flyball/<name>.
	sysd := shortTemp(t)
	os.Chmod(sysd, 0o700)
	t.Setenv("RUNTIME_DIRECTORY", sysd+":/elsewhere")
	if root := Root("1a2b3c4d"); root != sysd {
		t.Errorf("Root under systemd: %q, want %q", root, sysd)
	}

	// No runtime dir, one other users can write, or a path that would not
	// fit sun_path: an unpredictable temp dir instead.
	t.Setenv("RUNTIME_DIRECTORY", "")
	t.Setenv("XDG_RUNTIME_DIR", "")
	if root := Root("1a2b3c4d"); root != "" {
		t.Errorf("Root with no runtime dir: %q", root)
	}
	open := shortTemp(t)
	os.Chmod(open, 0o777)
	t.Setenv("XDG_RUNTIME_DIR", open)
	if root := Root("1a2b3c4d"); root != "" {
		t.Errorf("Root under a 0777 runtime dir: %q", root)
	}
	long := filepath.Join(run, strings.Repeat("d", 90))
	for _, r := range []string{"", long} {
		dir, err := Dir(r, "oven")
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { os.RemoveAll(dir) })
		if strings.HasPrefix(dir, long) || len(filepath.Join(dir, Sock)) > endpoint.MaxSocketPath {
			t.Errorf("Dir(%q): %q", r, dir)
		}
		if !strings.Contains(filepath.Base(dir), "flyball-") {
			t.Errorf("Dir(%q) = %q, want a flyball- temp dir", r, dir)
		}
		if err := Check(dir); err != nil {
			t.Errorf("temp front-dir: %v", err)
		}
	}
}
