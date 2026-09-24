// Package exposure is the loopback and same-site rules the front (package
// front) and the runner share: which listen addresses and Host names are
// loopback, whether an Origin is the request's own site, and the warnings
// a front prints when it is exposed. The front holds each runner's door
// itself (the signed principal), so nothing here probes a runner any more.
package exposure

import (
	"fmt"
	"net"
	"strings"
)

// IsLoopback reports whether a listen address reaches this machine only:
// localhost, 127.0.0.0/8 or ::1, with or without a port. An empty host
// (":8000"), 0.0.0.0, ::, a LAN address or any other name is reachable.
func IsLoopback(addr string) bool {
	host := addr
	if h, _, err := net.SplitHostPort(addr); err == nil {
		host = h
	}
	host = strings.Trim(strings.TrimSpace(host), "[]")
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// OpenWarning is logged when the local shape (no login) is served beyond
// loopback by choice (--insecure-open).
func OpenWarning(addr string) string {
	return fmt.Sprintf("serving an OPEN runner on %s (--insecure-open): anyone who can reach it may operate the rig", describe(addr))
}

// CleartextWarning is logged when credentials are served beyond loopback
// over plain HTTP.
func CleartextWarning(addr string) string {
	return fmt.Sprintf("serving plain HTTP on %s: the password, the token and session cookies"+
		" cross the network unencrypted; put TLS in front, or listen on 127.0.0.1", describe(addr))
}

func describe(addr string) string {
	if h, _, err := net.SplitHostPort(addr); err == nil && h == "" {
		return addr + " (every interface)"
	}
	return addr
}
