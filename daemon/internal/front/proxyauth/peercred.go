package proxyauth

import (
	"context"
	"crypto/tls"
	"net"
)

type uidKey struct{}

// ConnContext is an http.Server ConnContext that records a unix-socket
// peer's uid (SO_PEERCRED, Linux) for PeerUID. The server serving a
// `from: unix` front sets it; without it the uid is recorded as unknown.
func ConnContext(ctx context.Context, c net.Conn) context.Context {
	if tc, ok := c.(*tls.Conn); ok {
		c = tc.NetConn()
	}
	if uc, ok := c.(*net.UnixConn); ok {
		if uid, ok := peerUID(uc); ok {
			return context.WithValue(ctx, uidKey{}, uid)
		}
	}
	return ctx
}

// PeerUID is the uid ConnContext recorded for the request's connection.
func PeerUID(ctx context.Context) (uint32, bool) {
	uid, ok := ctx.Value(uidKey{}).(uint32)
	return uid, ok
}
