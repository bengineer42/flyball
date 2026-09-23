//go:build unix

package front

import (
	"fmt"
	"os"
	"runtime"
	"syscall"
)

// checkSocketDir refuses a directory for the front's unix socket that would
// let another local user pose as the proxy: one anyone may write to without
// the sticky bit (they could replace the socket), or, when the front owns
// it, one anyone may enter.
func checkSocketDir(dir string) error {
	fi, err := os.Stat(dir)
	if err != nil {
		return err
	}
	perm := fi.Mode().Perm()
	if perm&0o002 != 0 && fi.Mode()&os.ModeSticky == 0 {
		return fmt.Errorf("%s is world-writable without the sticky bit: anyone could replace the socket", dir)
	}
	if st, ok := fi.Sys().(*syscall.Stat_t); ok && int(st.Uid) == os.Geteuid() && perm&0o007 != 0 {
		return fmt.Errorf("%s is world-accessible (%v): give it no permissions for others (0750, or 0700)", dir, perm)
	}
	return nil
}

// socketControl gives the front's unix socket its mode before bind. On
// Linux bind creates the file with the socket's own mode less the umask,
// so it is never connectable with a wider one; Listen's chmod after bind
// then only adds back what the umask took. Elsewhere it does nothing, and
// that chmod is all there is.
func socketControl(_, _ string, c syscall.RawConn) error {
	if runtime.GOOS != "linux" {
		return nil
	}
	var err error
	if cerr := c.Control(func(fd uintptr) { err = syscall.Fchmod(int(fd), 0o660) }); cerr != nil {
		return cerr
	}
	return err
}
