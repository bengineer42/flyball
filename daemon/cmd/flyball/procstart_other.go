//go:build !darwin

package main

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// processStartTime is when pid started, for lockHolder where there is no
// /proc/locks: unknown here (Linux has /proc/locks; elsewhere nothing
// is signalled on the lock file's word alone); a variable for the tests.
var processStartTime = func(int) (time.Time, bool) { return time.Time{}, false }

// processName is pid's command name (/proc/<pid>/comm), "" where unknown.
func processName(pid int) string {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/comm")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// childOf is a child of pid (the lowest pid, from /proc/<n>/stat's ppid),
// 0 where there is none or it cannot be known.
func childOf(pid int) int {
	stats, _ := filepath.Glob("/proc/[0-9]*/stat")
	child := 0
	for _, p := range stats {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		s := string(b)
		i := strings.LastIndexByte(s, ')')
		if i < 0 {
			continue
		}
		f := strings.Fields(s[i+1:]) // [0] state, [1] ppid
		if len(f) < 2 || f[1] != strconv.Itoa(pid) {
			continue
		}
		if n, err := strconv.Atoi(filepath.Base(filepath.Dir(p))); err == nil && (child == 0 || n < child) {
			child = n
		}
	}
	return child
}
