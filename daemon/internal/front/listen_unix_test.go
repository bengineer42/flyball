//go:build unix

package front

import (
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

// sockDir is a fresh directory with mode perm, short enough for a unix
// socket path.
func sockDir(t *testing.T, perm os.FileMode) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "fb-ln-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	if err := os.Chmod(dir, perm); err != nil {
		t.Fatal(err)
	}
	return dir
}

// The front's unix socket is owner+group only, whatever the umask: an
// unsigned proxy preset believes whoever can connect to it.
func TestListenUnixSocketMode(t *testing.T) {
	old := syscall.Umask(0)
	defer syscall.Umask(old)
	path := filepath.Join(sockDir(t, 0o750), "front.sock")
	ln, err := Listen(Plan{Listen: "unix:" + path})
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := fi.Mode().Perm(); got != 0o660 {
		t.Fatalf("socket mode under umask 0: %v, want -rw-rw----", fi.Mode().Perm())
	}
}

// The socket's directory must not let another user replace the socket
// (world-writable without the sticky bit), nor, when the front owns it,
// let everyone reach it.
func TestListenUnixSocketDir(t *testing.T) {
	for _, tc := range []struct {
		perm os.FileMode
		want string // "" = listens
	}{
		{0o700, ""},
		{0o750, ""},
		{0o770, ""},
		{0o755, "world"},
		{0o701, "world"},
		{0o777, "world"},
		{0o777 | os.ModeSticky, "world"},
	} {
		dir := sockDir(t, tc.perm)
		ln, err := Listen(Plan{Listen: "unix:" + filepath.Join(dir, "front.sock")})
		switch {
		case tc.want == "" && err != nil:
			t.Errorf("%v: %v", tc.perm, err)
		case tc.want != "" && err == nil:
			t.Errorf("%v: listened, want a refusal", tc.perm)
		case tc.want != "" && !strings.Contains(err.Error(), tc.want):
			t.Errorf("%v: %v, want %q", tc.perm, err, tc.want)
		}
		if ln != nil {
			ln.Close()
		}
	}
	// Another user's world-writable directory with the sticky bit (/tmp)
	// is fine: nobody else can remove the front's socket from it.
	tmp, err := os.Stat("/tmp")
	if st, ok := tmp.Sys().(*syscall.Stat_t); err != nil || !ok || int(st.Uid) == os.Geteuid() || tmp.Mode()&os.ModeSticky == 0 {
		t.Skip("no sticky /tmp owned by another user")
	}
	ln, err := Listen(Plan{Listen: "unix:" + filepath.Join("/tmp", "fb-ln-"+filepath.Base(sockDir(t, 0o700))+".sock")})
	if err != nil {
		t.Fatalf("/tmp: %v", err)
	}
	ln.Close()
}
