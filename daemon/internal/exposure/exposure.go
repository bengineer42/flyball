// Package exposure decides whether a runner may be put on the network.
//
// A runner with no password and no token is open: anyone who reaches it
// may operate the rig. The runner itself serves open only on loopback
// (engine/src/flyball/runtime/config.py's settle_exposure); the two Go
// fronts -- `flyball run --serve-ui` and flyballd's routing -- proxy to a
// runner on loopback, so they must make the same decision for the address
// they listen on. They ask the runner rather than reading its file, since
// credentials may come from its environment (FLYBALL_PASSWORD,
// FLYBALL_TOKEN) as well: GET /api/auth says `password` and `token`.
package exposure

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strconv"
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

// Door is what a runner's GET /api/auth says it has.
type Door struct {
	Password bool
	Token    bool
}

// Open is no password and no token: anyone who reaches it may operate.
func (d Door) Open() bool { return !d.Password && !d.Token }

// ErrNotADoor wraps an answer from url that is not a runner's door: a
// status other than 200, or a body without `password` and `token`.
var ErrNotADoor = errors.New("not a flyball runner's /api/auth")

// Probe asks the runner at url (its /api/auth) what door it has. Anything
// but a 200 carrying both fields is an error wrapping ErrNotADoor; no
// answer at all is the client's error. A caller that cannot tell must
// treat the runner as open.
func Probe(ctx context.Context, client *http.Client, url string) (Door, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return Door{}, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return Door{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Door{}, fmt.Errorf("%w: GET %s: %s", ErrNotADoor, url, resp.Status)
	}
	var body struct {
		Password *bool `json:"password"`
		Token    *bool `json:"token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return Door{}, fmt.Errorf("%w: GET %s: %v", ErrNotADoor, url, err)
	}
	if body.Password == nil || body.Token == nil {
		return Door{}, fmt.Errorf("%w: GET %s: no password or token field", ErrNotADoor, url)
	}
	return Door{Password: *body.Password, Token: *body.Token}, nil
}

// RestrictedWarning is logged when an open runner, asked to be served on
// addr beyond loopback, is served on loopback instead.
func RestrictedWarning(addr, loopback string) string {
	return fmt.Sprintf("asked to serve on %s, but the runner has no password and no token:"+
		" serving on %s only, so nothing beyond this machine can reach the rig. To serve it on"+
		" the network give the runner a password or a token (runner.auth in the rig file,"+
		" --password/--token, FLYBALL_PASSWORD/FLYBALL_TOKEN; `flyball password` makes one),"+
		" or, knowingly, --insecure-open or FLYBALL_INSECURE_OPEN=1", describe(addr), loopback)
}

// OpenWarning is logged when an open runner is exposed by choice.
func OpenWarning(addr string) string {
	return fmt.Sprintf("serving an OPEN runner on %s (--insecure-open): anyone who can reach it may operate the rig", describe(addr))
}

// CleartextWarning is logged when credentials are served beyond loopback
// over plain HTTP.
func CleartextWarning(addr string) string {
	return fmt.Sprintf("serving plain HTTP on %s: the password, the token and session cookies"+
		" cross the network unencrypted; put TLS in front, or listen on 127.0.0.1", describe(addr))
}

// Plan is where a front asked to listen on Requested serves a runner.
type Plan struct {
	Requested string // the address asked for
	Addr      string // the address to listen on: Requested, or loopback for an open runner
	Open      bool   // the runner has no password and no token
	Warning   string // one line for stderr; "" for none
}

// Restricted is an open runner moved to loopback.
func (p Plan) Restricted() bool { return p.Addr != p.Requested }

// OpenNetwork is an open runner reachable beyond this machine, by choice.
func (p Plan) OpenNetwork() bool { return p.Open && !IsLoopback(p.Addr) }

// Exposure is the plan as the runner's GET /api/auth and /api/health
// report it (`exposure`), so the dashboard can warn.
func (p Plan) Exposure() map[string]any {
	host, port, _ := net.SplitHostPort(p.Addr)
	n, _ := strconv.Atoi(port)
	var warning any
	if p.Warning != "" {
		warning = p.Warning
	}
	return map[string]any{
		"requested": p.Requested, "host": host, "port": n, "open": p.Open,
		"restricted": p.Restricted(), "open_network": p.OpenNetwork(), "warning": warning,
	}
}

// Decide is the front's rule for a runner with door d behind addr. An
// auth misconfiguration removes exposure, never operation: an open runner
// beyond loopback is served on 127.0.0.1 (the same port) unless
// insecureOpen, never refused.
func Decide(addr string, d Door, insecureOpen bool) Plan {
	plan := Plan{Requested: addr, Addr: addr, Open: d.Open()}
	switch {
	case IsLoopback(addr):
	case d.Open() && !insecureOpen:
		_, port, _ := net.SplitHostPort(addr)
		plan.Addr = net.JoinHostPort("127.0.0.1", port)
		plan.Warning = RestrictedWarning(addr, plan.Addr)
	case d.Open():
		plan.Warning = OpenWarning(addr)
	default:
		plan.Warning = CleartextWarning(addr)
	}
	return plan
}

func describe(addr string) string {
	if h, _, err := net.SplitHostPort(addr); err == nil && h == "" {
		return addr + " (every interface)"
	}
	return addr
}
