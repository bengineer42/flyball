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
