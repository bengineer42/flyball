// Package endpoint is where a runner listens, as the front reaches it: a
// unix socket in the runner's front-dir (everywhere but Windows), or TCP
// on loopback (Windows only, D-044). One type replaces the host:port that
// used to be assumed wherever a runner was dialled.
//
// The string form, "unix:/abs/path" or "tcp:127.0.0.1:8102", is what the
// front writes to <front-dir>/endpoint, what the runner reads back to
// decide what to bind, and what flyballd's GET /api/runners reports.
package endpoint

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// MaxSocketPath is the longest unix socket path accepted, in bytes.
// sun_path is 108 bytes on Linux (107 usable) and 104 on macOS; 100
// leaves room on both.
const MaxSocketPath = 100

// GOOS is the platform D-044's tcp rule is decided for: runtime.GOOS. A
// test sets it to "windows" to take that path without building for it.
var GOOS = runtime.GOOS

// ErrTCPRefused is a tcp endpoint where TCPAllowed is false.
var ErrTCPRefused = errors.New("network tcp is refused except on Windows until the runner proves it holds the key (D-044);" +
	" use the default, a unix socket in the runner's front-dir")

// TCPAllowed reports whether a front may reach its runner over tcp here:
// on Windows only, where the runner has no unix sockets (D-044). Over tcp
// the runner never proves it holds the key, so a local user that binds
// the port first passes the handshake; a unix socket in the 0700
// front-dir has no such gap, and anything that can share the front-dir
// can use one.
func TCPAllowed() bool { return GOOS == "windows" }

// Endpoint is one runner's listening address.
type Endpoint struct {
	Network string `json:"network"` // "unix" | "tcp"
	Address string `json:"address"` // unix: absolute socket path; tcp: "127.0.0.1:8102" (loopback only)
}

// Parse reads the string form. It refuses a relative or unclean socket
// path, one over MaxSocketPath bytes, a tcp address that is not
// loopback (an IP literal or "localhost") with a numeric port, any tcp
// endpoint off Windows (ErrTCPRefused), and anything else.
func Parse(s string) (Endpoint, error) {
	network, address, ok := strings.Cut(s, ":")
	if !ok {
		return Endpoint{}, fmt.Errorf("endpoint %q: want unix:/abs/path or tcp:127.0.0.1:port", s)
	}
	e := Endpoint{Network: network, Address: address}
	if err := e.Validate(); err != nil {
		return Endpoint{}, err
	}
	return e, nil
}

// Validate applies Parse's rules to an Endpoint built by hand.
func (e Endpoint) Validate() error {
	switch e.Network {
	case "unix":
		p := e.Address
		switch {
		case p == "" || !filepath.IsAbs(p):
			return fmt.Errorf("endpoint unix:%s: the socket path must be absolute", p)
		case filepath.Clean(p) != p:
			return fmt.Errorf("endpoint unix:%s: the socket path must be clean (no .., //, or trailing /)", p)
		case strings.ContainsRune(p, 0):
			return fmt.Errorf("endpoint unix:%q: NUL in the socket path", p)
		case len(p) > MaxSocketPath:
			return fmt.Errorf("endpoint unix:%s: the socket path is %d bytes; at most %d fit sun_path", p, len(p), MaxSocketPath)
		}
		return nil
	case "tcp":
		host, port, err := net.SplitHostPort(e.Address)
		if err != nil {
			return fmt.Errorf("endpoint tcp:%s: %v", e.Address, err)
		}
		if !loopback(host) {
			return fmt.Errorf("endpoint tcp:%s: %q is not a loopback address; a runner listens on loopback only", e.Address, host)
		}
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 || strconv.Itoa(n) != port {
			return fmt.Errorf("endpoint tcp:%s: port %q is not a TCP port", e.Address, port)
		}
		if !TCPAllowed() {
			return fmt.Errorf("endpoint tcp:%s: %w", e.Address, ErrTCPRefused)
		}
		return nil
	}
	return fmt.Errorf("endpoint %q: network %q: use unix or tcp", e.String(), e.Network)
}

func loopback(host string) bool {
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// String is the inverse of Parse.
func (e Endpoint) String() string { return e.Network + ":" + e.Address }

// Transport is an http.Transport whose every connection dials e, whatever
// host the request's URL names, and which never goes through an
// HTTP_PROXY from the environment.
func (e Endpoint) Transport() *http.Transport {
	network, address := e.Network, e.Address
	return &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, network, address)
		},
		MaxIdleConns:          16,
		IdleConnTimeout:       90 * time.Second,
		ExpectContinueTimeout: time.Second,
	}
}

// URL is the base URL for a request to the runner under rootPath:
// "http://localhost" + rootPath, for both networks. The host is only a
// name for the Host header; Transport decides where the bytes go.
func (e Endpoint) URL(rootPath string) string { return "http://localhost" + rootPath }
