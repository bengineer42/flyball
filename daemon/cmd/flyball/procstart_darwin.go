package main

import (
	"time"

	"golang.org/x/sys/unix"
)

// processStartTime is when pid started (sysctl kern.proc.pid), for
// lockHolder where there is no /proc/locks; a variable for the tests.
var processStartTime = func(pid int) (time.Time, bool) {
	kp, err := unix.SysctlKinfoProc("kern.proc.pid", pid)
	if err != nil || kp.Proc.P_pid != int32(pid) {
		return time.Time{}, false
	}
	tv := kp.Proc.P_starttime
	return time.Unix(int64(tv.Sec), int64(tv.Usec)*1000), true
}

// processName is pid's command name (sysctl kern.proc.pid), "" where unknown.
func processName(pid int) string {
	kp, err := unix.SysctlKinfoProc("kern.proc.pid", pid)
	if err != nil || kp.Proc.P_pid != int32(pid) {
		return ""
	}
	return unix.ByteSliceToString(kp.Proc.P_comm[:])
}

// childOf is a child of pid (the lowest pid), 0 where there is none or it
// cannot be known.
func childOf(pid int) int {
	procs, err := unix.SysctlKinfoProcSlice("kern.proc.all")
	if err != nil {
		return 0
	}
	child := 0
	for _, kp := range procs {
		if n := int(kp.Proc.P_pid); int(kp.Eproc.Ppid) == pid && (child == 0 || n < child) {
			child = n
		}
	}
	return child
}
