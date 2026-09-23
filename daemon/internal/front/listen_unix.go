//go:build unix

package front

import (
	"fmt"
	"os"
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
