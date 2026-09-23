//go:build unix

package frontwire

import (
	"io/fs"
	"os"
	"syscall"
)

// ownedByMe: fi's owner is this process's euid.
func ownedByMe(fi fs.FileInfo) bool {
	st, ok := fi.Sys().(*syscall.Stat_t)
	return ok && int(st.Uid) == os.Geteuid()
}
