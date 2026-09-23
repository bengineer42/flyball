// Package frontwire is what the two fronts, `flyball run` and flyballd,
// share around package front: reading a front block, resolving it with
// the proxy presets, where the front keeps its state, and opening the
// front with its audit and tokens.
package frontwire

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front"
	"flyballd/internal/front/proxyauth"
	"flyballd/internal/webui"

	"gopkg.in/yaml.v3"
)

// Presets are the trusted-header presets (package front/proxyauth) as both
// fronts use them: Plan resolves through Presets.Factory, and Serve's
// server sets Presets.ConnContext on each connection (proxyauth reads the
// SO_PEERCRED uid of a `from: unix` peer from it). The zero Hooks is a
// build without presets: a proxy shape falls back to local on loopback.
var Presets = Hooks{
	Factory: func(o ProxyOptions) front.ProxyFactory {
		return proxyauth.Factory(proxyauth.Options{Logger: o.Logger, Audit: o.Audit})
	},
	ConnContext: proxyauth.ConnContext,
}

// Hooks are what the presets plug in.
type Hooks struct {
	Factory     func(ProxyOptions) front.ProxyFactory
	ConnContext func(ctx context.Context, c net.Conn) context.Context
}

// ProxyOptions are what a preset factory is built with.
type ProxyOptions struct {
	Logger *slog.Logger
	Audit  *front.Audit // nil: proxy events go to Logger
}

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

// Plan resolves c with the presets (Presets.Factory, built with o). bad is
// why c's block could not be read (Decode's error): then the front serves
// the local shape on a fresh loopback address and answers 503 on c.Listen,
// with bad in the banner -- the block may have asked for password or proxy,
// and a reverse proxy may still forward to c.Listen; a front
// misconfiguration never stops the rig (D-028, amended: sec F1).
func Plan(c front.Config, bad error, insecureOpen bool, o ProxyOptions) (front.Plan, front.Client) {
	if bad == nil {
		var factory front.ProxyFactory
		if Presets.Factory != nil {
			factory = Presets.Factory(o)
		}
		return front.ResolveWith(c, insecureOpen, factory)
	}
	requested := c.Listen
	if requested == "" {
		requested = front.DefaultListen
	}
	// Not a shape: ResolveWith falls back as for any credential shape.
	p, _ := front.ResolveWith(front.Config{Listen: requested, Auth: "unreadable"}, false, nil)
	p.Requested = requested
	p.Fallback = fmt.Sprintf("the front's configuration cannot be read (%v)", bad)
	return p, nil
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

// StateHome is $XDG_STATE_HOME, else ~/.local/state. With neither
// (no HOME: a unit file or cron without it) it is an error: never a
// shared, predictable directory such as one under $TMPDIR, for what is
// kept there (named tokens, the audit, run.log).
func StateHome() (string, error) {
	if s := os.Getenv("XDG_STATE_HOME"); s != "" && filepath.IsAbs(s) {
		return s, nil
	}
	home, err := os.UserHomeDir()
	if err != nil || !filepath.IsAbs(home) {
		return "", errors.New("no state directory: set HOME, or XDG_STATE_HOME to an absolute path")
	}
	return filepath.Join(home, ".local", "state"), nil
}

// RunDir is where `flyball run` keeps a front's tokens and audit:
// <StateHome>/flyball/front-<frontID>, frontID being
// frontdir.FrontID(the first rig file).
func RunDir(frontID string) (string, error) {
	home, err := StateHome()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, "flyball", "front-"+frontID), nil
}

// RunRig is the rig name a `flyball run` front-dir is made for.
const RunRig = "run"

// RunFrontDir is the front-dir `flyball run RIG` gives its runner, derived
// from the rig file's path alone: <frontdir.Root(FrontID(rig))>/run. ok is
// false when there is no private runtime dir, or the socket path would be
// too long: then each run gets a random temp dir, which cannot be derived.
func RunFrontDir(rig string) (dir string, ok bool) {
	id, err := frontdir.FrontID(rig)
	if err != nil {
		return "", false
	}
	root := frontdir.Root(id)
	if root == "" || !filepath.IsAbs(root) {
		return "", false
	}
	dir = filepath.Join(root, RunRig)
	if len(filepath.Join(dir, frontdir.Sock)) > endpoint.MaxSocketPath {
		return "", false
	}
	return dir, true
}

// DaemonDir is where flyballd keeps them: <data_dir>/front, absolute.
func DaemonDir(dataDir string) string {
	abs, err := filepath.Abs(filepath.Join(dataDir, "front"))
	if err != nil {
		return filepath.Join(dataDir, "front")
	}
	return abs
}

// OpenAudit opens the front's audit at dir/audit.jsonl. One that cannot be
// opened is logged and the front runs on (D-028: the rig is still served)
// with a front.FailedAudit: sign-ins and token changes, which must be
// recorded, answer 503.
func OpenAudit(dir string, logger *slog.Logger) *front.Audit {
	audit, err := front.OpenAudit(filepath.Join(dir, AuditFile))
	if err != nil {
		orDefault(logger).Error("front: no audit log; sign-ins and token changes are refused", "err", err)
		return front.FailedAudit(err)
	}
	return audit
}

// Options are the front's options over plan: its named tokens at
// dir/tokens.json, its audit, the embedded UI. The caller adds Route and
// Fallback.
func Options(plan front.Plan, proxy front.Client, audit *front.Audit, dir string, logger *slog.Logger) front.Options {
	ui, err := fs.Sub(webui.Dist, "dist")
	if err != nil {
		ui = nil
	}
	return front.Options{
		Plan: plan, Proxy: proxy, TokensPath: filepath.Join(dir, TokensFile), Audit: audit, UI: ui,
		Logger: orDefault(logger),
	}
}

// Closer closes the front, the plan's TLS reloader and the audit.
func Closer(f *front.Front, plan front.Plan, audit *front.Audit) func() {
	return func() {
		f.Close()
		plan.Close()
		audit.Close()
	}
}

// Serve is front.Serve with Presets.ConnContext on the server: it listens
// on the plan's address, says where (ready, if not nil), and serves h
// until ctx is done, then shuts down (5 s grace).
func Serve(ctx context.Context, p front.Plan, h http.Handler, ready func(net.Addr)) error {
	ln, err := front.Listen(p)
	if err != nil {
		return err
	}
	if bf, ok := h.(interface{ Bound(net.Addr) }); ok {
		bf.Bound(ln.Addr())
	}
	if ready != nil {
		ready(ln.Addr())
	}
	srv := front.NewServer(p, h)
	srv.ConnContext = Presets.ConnContext
	errc := make(chan error, 1)
	go func() { errc <- srv.Serve(ln) }()
	select {
	case err := <-errc:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case <-ctx.Done():
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return srv.Shutdown(sctx)
	}
}

func orDefault(l *slog.Logger) *slog.Logger {
	if l == nil {
		return slog.Default()
	}
	return l
}
