//go:build !unix

package front

import "syscall"

// checkSocketDir checks nothing where there are no unix permissions.
func checkSocketDir(string) error { return nil }

// socketControl does nothing where there are no unix permissions.
func socketControl(string, string, syscall.RawConn) error { return nil }
