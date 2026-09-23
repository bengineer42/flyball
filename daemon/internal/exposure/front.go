package exposure

import (
	"net/netip"
	"net/url"
	"os"
	"slices"
	"strconv"
	"strings"
)

// The Host and Origin rules of the local shape and the bare runner
// (engine/src/flyball/interfaces/server/auth.py), shared by the front's
// CheckHost and CheckOrigin (package front).

// loopbackNames are the runner's own (auth.py's LOOPBACK).
var loopbackNames = map[string]bool{"localhost": true, "127.0.0.1": true, "::1": true}

var defaultPorts = map[string]int{"http": 80, "https": 443}

// authority is host[:port] as (lower-case name, port), the port defaulted
// for scheme; ok is false for anything that is not a plain authority.
func authority(netloc, scheme string) (name string, port int, ok bool) {
	u, err := url.Parse("//" + netloc)
	if err != nil || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Hostname() == "" {
		return "", 0, false
	}
	port = defaultPorts[scheme]
	if p := u.Port(); p != "" {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 || n > 65535 {
			return "", 0, false
		}
		port = n
	}
	return strings.ToLower(u.Hostname()), port, true
}

// LoopbackName reports whether a Host header names loopback as the runner
// counts it: localhost, 127.0.0.1 or [::1], any port.
func LoopbackName(host string) bool {
	name, _, ok := authority(host, "http")
	return ok && loopbackNames[name]
}

// OwnNames is this machine's names (auth.py's own_names): the hostname,
// its first label, and each with `.local` (mDNS), lower case. None when
// the hostname cannot be read.
func OwnNames() []string {
	name, err := os.Hostname()
	if err != nil {
		return nil
	}
	name = strings.ToLower(name)
	first, _, _ := strings.Cut(name, ".")
	var names []string
	for _, n := range []string{name, first} {
		if n != "" && !slices.Contains(names, n) {
			names = append(names, n, n+".local")
		}
	}
	return names
}

// KnownHost reports whether a Host header is a name no page elsewhere can
// own, any port (auth.py's known_host): an IP address, a loopback name, or
// one of names (OwnNames). Anything else is a DNS name, which whoever owns
// it may point at this machine (DNS rebinding, D-043).
func KnownHost(host string, names []string) bool {
	name, _, ok := authority(host, "http")
	if !ok {
		return false
	}
	if loopbackNames[name] || slices.Contains(names, name) {
		return true
	}
	_, err := netip.ParseAddr(name)
	return err == nil
}

// SameSite reports whether origin is the site a request to host over
// scheme came from, by the runner's rule (auth.py's same_origin): an http
// or https origin whose host (any case) and port (defaulted per scheme)
// are host's, and whose scheme is the request's -- or https on an http
// request, a TLS proxy in front. "" and "null" are not.
func SameSite(origin, host, scheme string) bool {
	own := "http"
	if scheme == "https" || scheme == "wss" {
		own = "https"
	}
	u, err := url.Parse(origin)
	if err != nil {
		return false
	}
	if _, known := defaultPorts[u.Scheme]; !known || (u.Scheme != own && own == "https") {
		return false
	}
	theirs, theirPort, ok := authority(u.Host, u.Scheme)
	if !ok {
		return false
	}
	ours, ourPort, ok := authority(host, u.Scheme)
	return ok && theirs == ours && theirPort == ourPort
}
