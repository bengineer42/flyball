//go:build unix

package backend

import (
	"errors"
	"os"
	"os/exec"
	"strconv"
	"strings"
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

// killGroup SIGKILLs p's process group (ownGroup made p its leader), so
// a runner under uv goes with uv.
func killGroup(p *os.Process) error {
	return syscall.Kill(-p.Pid, syscall.SIGKILL)
}

// processStart is pid's start time (field 22 of /proc/<pid>/stat), which
// tells the process apart from a later one given the same pid; "" where
// there is no /proc.
func processStart(pid int) string {
	st, ok := procStat(pid)
	if !ok || len(st) < 20 {
		return ""
	}
	return st[19]
}

// procStat is /proc/<pid>/stat's fields after the command name: [0] is
// the state, [19] the start time.
func procStat(pid int) ([]string, bool) {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return nil, false
	}
	s := string(b)
	i := strings.LastIndexByte(s, ')')
	if i < 0 {
		return nil, false
	}
	return strings.Fields(s[i+1:]), true
}

// processAlive says whether pid is a process that has not exited: kill -0
// finds it, it is not a zombie, and, when start is known, it is the same
// process that started then (not a later one with its pid).
func processAlive(pid int, start string) bool {
	if pid <= 0 {
		return false
	}
	if err := syscall.Kill(pid, 0); err != nil && !errors.Is(err, syscall.EPERM) {
		return false
	}
	st, ok := procStat(pid)
	if !ok {
		_, err := os.Stat("/proc/self/stat")
		return err != nil // no /proc at all: kill -0 is all there is
	}
	if len(st) > 0 && st[0] == "Z" {
		return false
	}
	return start == "" || (len(st) >= 20 && st[19] == start)
}
