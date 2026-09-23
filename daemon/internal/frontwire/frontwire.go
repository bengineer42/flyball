// Package frontwire is what the two fronts, `flyball run` and flyballd,
// share around package front: reading a front block, resolving it with
// the proxy presets, where the front keeps its state, and opening the
// front with its audit and tokens.
package frontwire

import (
	"bytes"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"flyballd/internal/front"
	"flyballd/internal/webui"

	"gopkg.in/yaml.v3"
)

// ProxyFactory builds the proxy shape's provider from `proxy:`. Both fronts
// resolve through it (Plan). nil: this build has no presets, and a proxy
// shape falls back to local on loopback. The trusted-header presets
// (package front/proxyauth) are wired in by setting it here.
var ProxyFactory front.ProxyFactory

// The files the front keeps in its state directory (RunDir, DaemonDir).
const (
	TokensFile = "tokens.json"
	AuditFile  = "audit.jsonl"
)

// Decode reads a front block -- a rig file's `runner.front`, or the front
// keys of flyballd.yaml -- into a front.Config. As the Python model
// (FrontConfig, extra="forbid"), an unknown key or a wrong type is an
// error; the caller hands it to Plan, which falls back (D-028).
func Decode(block map[string]any) (front.Config, error) {
	var c front.Config
	if len(block) == 0 {
		return c, nil
	}
	raw, err := yaml.Marshal(block)
	if err != nil {
		return c, err
	}
	dec := yaml.NewDecoder(bytes.NewReader(raw))
	dec.KnownFields(true)
	if err := dec.Decode(&c); err != nil {
		return front.Config{}, fmt.Errorf("%s", strings.TrimPrefix(err.Error(), "yaml: "))
	}
	return c, nil
}

// Plan resolves c with ProxyFactory. bad is why c's block could not be
// read (Decode's error): then the front serves the local shape on
// loopback, on c.Listen's port, with bad in the banner -- a front
// misconfiguration never stops the rig (D-028).
func Plan(c front.Config, bad error, insecureOpen bool) (front.Plan, front.Client) {
	if bad == nil {
		return front.ResolveWith(c, insecureOpen, ProxyFactory)
	}
	requested := c.Listen
	if requested == "" {
		requested = front.DefaultListen
	}
	p, _ := front.ResolveWith(front.Config{Listen: loopbackOf(requested)}, false, nil)
	p.Requested = requested
	p.Fallback = fmt.Sprintf("the front's configuration cannot be read (%v)", bad)
	return p, nil
}

// loopbackOf is listen moved to 127.0.0.1, keeping its port; a unix socket
// is kept.
func loopbackOf(listen string) string {
	if strings.HasPrefix(listen, "unix:") {
		return listen
	}
	_, port, err := net.SplitHostPort(listen)
	if err != nil {
		return front.DefaultListen
	}
	return net.JoinHostPort("127.0.0.1", port)
}

// InsecureOpenEnv is FLYBALL_INSECURE_OPEN set to 1/true/yes/on: the
// per-invocation opt-in to serve the local shape beyond loopback, as
// --insecure-open. Never a file key.
func InsecureOpenEnv() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("FLYBALL_INSECURE_OPEN"))) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}

// StateHome is $XDG_STATE_HOME, else ~/.local/state.
func StateHome() string {
	if s := os.Getenv("XDG_STATE_HOME"); s != "" && filepath.IsAbs(s) {
		return s
	}
	home, err := os.UserHomeDir()
	if err != nil {
		home = os.TempDir()
	}
	return filepath.Join(home, ".local", "state")
}

// RunDir is where `flyball run` keeps a front's tokens and audit:
// <StateHome>/flyball/front-<frontID>, frontID being
// frontdir.FrontID(the first rig file).
func RunDir(frontID string) string {
	return filepath.Join(StateHome(), "flyball", "front-"+frontID)
}

// DaemonDir is where flyballd keeps them: <data_dir>/front, absolute.
func DaemonDir(dataDir string) string {
	abs, err := filepath.Abs(filepath.Join(dataDir, "front"))
	if err != nil {
		return filepath.Join(dataDir, "front")
	}
	return abs
}

// Open opens the front: its audit at dir/audit.jsonl, its named tokens at
// dir/tokens.json, the embedded UI. An audit that cannot be opened is
// logged and the front runs without one (D-028: the rig is still served).
// closeAll closes the front, the plan's TLS reloader and the audit.
func Open(plan front.Plan, proxy front.Client, dir string, route func(string) (front.Rig, bool), fallback http.Handler, logger *slog.Logger) (f *front.Front, closeAll func()) {
	if logger == nil {
		logger = slog.Default()
	}
	audit, err := front.OpenAudit(filepath.Join(dir, AuditFile))
	if err != nil {
		logger.Error("front: no audit log", "err", err)
		audit = nil
	}
	ui, err := fs.Sub(webui.Dist, "dist")
	if err != nil {
		ui = nil
	}
	f = front.New(front.Options{
		Plan: plan, Proxy: proxy, Route: route, Fallback: fallback,
		TokensPath: filepath.Join(dir, TokensFile), Audit: audit, UI: ui, Logger: logger,
	})
	return f, func() {
		f.Close()
		plan.Close()
		audit.Close()
	}
}
