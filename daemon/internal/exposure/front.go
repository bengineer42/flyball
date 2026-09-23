package exposure

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

// An open runner answers only a loopback Host and refuses anything that
// acts with an Origin that is not its own (engine/src/flyball/interfaces/
// server/auth.py). A Go front on another address or name passes the
// browser's Host through, so the runner would refuse every request made to
// the front by a network name. Front translates, but only where the user's
// choice or the front's own loopback already vouches for the name: the
// rebinding protection stays wherever it was not opted out of.

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

// Front is what a Go front (`flyball run --serve-ui`, flyballd's routing)
// does to a request before proxying it to a runner on loopback.
type Front struct {
	// Upstream is the runner: http://127.0.0.1:PORT.
	Upstream *url.URL
	// OpenNetwork is the front serving an open runner beyond loopback by
	// the user's choice (--insecure-open, auth.insecure_open).
	OpenNetwork bool
	// Door says what door the runner has; an error counts as open.
	Door func(ctx context.Context) (Door, error)
}

// Director wraps next (a ReverseProxy's Director) with Translate.
func (f *Front) Director(next func(*http.Request)) func(*http.Request) {
	return func(out *http.Request) {
		next(out)
		f.Translate(out)
	}
}

// Translate rewrites out, the request to be proxied (Host still the
// browser's), for an open runner:
//
//   - a Host that is not loopback, on a front that was not opted in, is
//     passed through untouched: the runner refuses it (DNS rebinding);
//   - otherwise Host becomes the runner's own address, and an Origin that
//     is same-site with the incoming Host becomes the runner's own origin,
//     so the runner's Origin check passes for the front's own pages; any
//     other Origin is left for the runner to refuse ("null" if it happens
//     to be the runner's own, which a page on the front's site cannot be).
//
// A runner with a password or a token is judged on the Host it was reached
// by, as it would be directly: nothing is changed.
func (f *Front) Translate(out *http.Request) {
	host := out.Host
	if !LoopbackName(host) && !f.OpenNetwork {
		return
	}
	if f.Door != nil {
		if door, err := f.Door(out.Context()); err == nil && !door.Open() {
			return
		}
	}
	scheme := "http"
	if out.TLS != nil {
		scheme = "https"
	}
	upstream := f.Upstream.Scheme + "://" + f.Upstream.Host
	out.Host = f.Upstream.Host
	origins := out.Header.Values("Origin")
	if len(origins) == 0 {
		return
	}
	switch {
	case len(origins) == 1 && SameSite(origins[0], host, scheme):
		out.Header.Set("Origin", upstream)
	case len(origins) > 1 || SameSite(origins[0], f.Upstream.Host, "http"):
		out.Header.Set("Origin", "null")
	}
}

// Doors asks runners what door they have (Probe), remembering each answer
// for TTL, so a front can ask on every request.
type Doors struct {
	Client *http.Client
	TTL    time.Duration

	mu   sync.Mutex
	seen map[string]seenDoor
}

type seenDoor struct {
	door Door
	at   time.Time
}

// NewDoors asks with a 2 s timeout and remembers for 2 s.
func NewDoors() *Doors {
	return &Doors{Client: &http.Client{Timeout: 2 * time.Second}, TTL: 2 * time.Second}
}

// Get is the door of the runner whose GET /api/auth is url. An error (no
// answer, or not a door) is not remembered.
func (d *Doors) Get(ctx context.Context, url string) (Door, error) {
	d.mu.Lock()
	s, ok := d.seen[url]
	d.mu.Unlock()
	if ok && time.Since(s.at) < d.TTL {
		return s.door, nil
	}
	door, err := Probe(ctx, d.Client, url)
	if err != nil {
		return Door{}, err
	}
	d.mu.Lock()
	if d.seen == nil {
		d.seen = map[string]seenDoor{}
	}
	d.seen[url] = seenDoor{door, time.Now()}
	d.mu.Unlock()
	return door, nil
}
