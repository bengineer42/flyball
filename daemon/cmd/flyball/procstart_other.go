//go:build !darwin

package main

import "time"

// processStartTime is when pid started, for lockHolder where there is no
// /proc/locks: unknown here (Linux has /proc/locks; elsewhere nothing
// is signalled on the lock file's word alone); a variable for the tests.
var processStartTime = func(int) (time.Time, bool) { return time.Time{}, false }
