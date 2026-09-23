//go:build !linux

package proxyauth

import "net"

// peerUID: SO_PEERCRED is Linux's; elsewhere the uid is not recorded.
func peerUID(*net.UnixConn) (uint32, bool) { return 0, false }
