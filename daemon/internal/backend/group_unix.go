//go:build unix

package backend

import (
	"os/exec"
	"syscall"
)

// ownGroup starts cmd in a process group of its own, so a signal to
// flyballd's group (Ctrl-C in its terminal, a kill -- -PGID) never reaches
// a runner: flyballd leaves its runners running (D-037). Not a session of
// its own: a session leader that opens a serial port without O_NOCTTY
// would take it as its controlling terminal and die of SIGHUP on a
// hang-up.
func ownGroup(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.Setpgid = true
}
