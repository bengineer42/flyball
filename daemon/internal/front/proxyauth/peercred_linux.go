//go:build linux

package proxyauth

import (
	"net"

	"golang.org/x/sys/unix"
)

// peerUID is the connecting process's uid, from SO_PEERCRED: set by the
// kernel at connect(2), not by the peer.
func peerUID(c *net.UnixConn) (uint32, bool) {
	raw, err := c.SyscallConn()
	if err != nil {
		return 0, false
	}
	var cred *unix.Ucred
	var cerr error
	if err := raw.Control(func(fd uintptr) {
		cred, cerr = unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
	}); err != nil || cerr != nil {
		return 0, false
	}
	return cred.Uid, true
}
