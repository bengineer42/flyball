// Package front is the door (brain design auth.md): the one component that
// authenticates people and machines, shared by `flyball run` and flyballd.
//
// For each request it checks the path (no `..`, `%2e`, `%2f`, `%5c`),
// the Host and, for anything that acts, the Origin; runs the provider
// chain (bearer token → session cookie → proxy assertion → anonymous);
// answers its own `<root>/api/auth*`; and proxies `<root>/api`, `/ws` and
// `/mcp` to the rig's runner over its endpoint with a freshly minted
// X-Flyball-Principal carrying the caller's verbs on that rig. It makes no
// path-based allow decision: the runner decides which verb each route
// needs. Everything else under a rig's root is the embedded UI.
package front

import (
	"context"
	"crypto/rand"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/exposure"
	"flyballd/internal/front/store"
	"flyballd/internal/principal"
)

// Target is where one rig's runner is now: its endpoint, the aud it
// verifies and the key of its current incarnation (backend.Channel's
// fields). Ask again for every request: the key changes at every spawn.
type Target struct {
	Endpoint endpoint.Endpoint
	Aud      string
	Key      principal.Key
}

var (
	// ErrStarting: the runner is not listening yet (503, Retry-After).
	ErrStarting = errors.New("the runner is starting")
	// ErrTooOld: the runner answered the readiness handshake, but not as a
	// fronted runner of this front (502; never proxied to, adv-blind 2).
	ErrTooOld = errors.New("runner too old for this front")
	// ErrNotRunning: the runner is stopped, failed or busy (503).
	ErrNotRunning = errors.New("the runner is not running")
)

// Rig is one rig the front serves.
type Rig struct {
	// Root is the rig's root path: "" for a front serving one rig at "/"
	// (`flyball run`), "/<name>" under flyballd.
	Root string
	// Name is what a scope's rig part names (`operate:<name>`): the
	// manifest name under flyballd.
	Name string
	// Target says where the runner is now; an error wrapping ErrStarting,
	// ErrNotRunning or ErrTooOld picks the answer.
	Target func(ctx context.Context) (Target, error)
}

// SingleRig routes every path to r.
func SingleRig(r Rig) func(string) (Rig, bool) {
	return func(string) (Rig, bool) { return r, true }
}

// Options configure a Front.
type Options struct {
	Plan Plan
	// Proxy is the proxy shape's provider (ResolveWith's second result).
	Proxy Client
	// Route finds the rig that owns a path (longest root first; flyballd
	// refuses overlapping roots at registration).
	Route func(path string) (Rig, bool)
	// Fallback serves paths no rig owns and that are not /api/auth*
	// (flyballd's landing and management API). nil: 404. It runs after the
	// path and Host checks.
	Fallback http.Handler
	// TokensPath is the named-tokens file; "" means no named tokens.
	TokensPath string
	// Audit receives authentication events; nil writes none.
	Audit *Audit
	// UI is the dashboard (webui.Dist's "dist"); a tree without
	// index.html is the placeholder build, refused with a page saying so.
	UI fs.FS
	// Logger is for operational messages; nil: slog.Default().
	Logger *slog.Logger

	// Now is the principal's clock; nil: time.Now.
	Now func() time.Time
	// TokensNow is the tokens file's clock; nil: time.Now. (Tests.)
	TokensNow func() time.Time
	// FailDelay follows a wrong password; 0: 500 ms.
	FailDelay time.Duration
	// Sweep is how often expired sessions and revoked or expired tokens
	// are looked for; 0: 250 ms (merge requirement 11: within 1 s).
	Sweep time.Duration
}

// Front is the door. It is an http.Handler.
type Front struct {
	o        Options
	plan     Plan
	log      *slog.Logger
	now      func() time.Time
	sessions *store.Sessions
	tokens   *store.Tokens
	tokErr   error
	limiter  *store.Limiter
	hasher   *store.Hasher
	cancels  *cancels
	cookie   string
	localSid string
	names    []string // this machine's own names (exposure.OwnNames): known Hosts, D-043
	signer   endpoint.Signer
	ui       http.Handler
	noUI     bool
	refused  *http.Server // 503 on Plan.Refused (holdRefused)

	mu         sync.Mutex
	verified   map[string][32]byte // endpoint|aud -> the key whose handshake passed
	transports map[string]*http.Transport

	stop chan struct{}
	done chan struct{}
	once sync.Once
}

// New builds the front and starts its sweeper; Close stops it. It never
// fails: an unreadable tokens file is logged, and every bearer token is
// then answered 503 (F10), never anonymous.
func New(o Options) *Front {
	f := &Front{
		o: o, plan: o.Plan, log: o.Logger, now: o.Now,
		limiter:  store.NewLimiter(store.LoginAttempts, store.LoginWindow, store.LimiterEntries, nil),
		hasher:   store.NewHasher(store.HashingSlots),
		cancels:  newCancels(),
		localSid: randomHex(16),
		names:    exposure.OwnNames(),
		verified: map[string][32]byte{}, transports: map[string]*http.Transport{},
		stop: make(chan struct{}), done: make(chan struct{}),
	}
	if f.log == nil {
		f.log = slog.Default()
	}
	if f.now == nil {
		f.now = time.Now
	}
	if f.o.FailDelay == 0 {
		f.o.FailDelay = 500 * time.Millisecond
	}
	if f.o.Sweep == 0 {
		f.o.Sweep = 250 * time.Millisecond
	}
	if f.o.Route == nil {
		f.o.Route = func(string) (Rig, bool) { return Rig{}, false }
	}
	f.signer = ProbeSigner(f.now)
	f.cookie = cookieName(f.plan)
	f.sessions = store.NewSessions(store.SessionOptions{Idle: f.plan.SessionIdle, OnEnd: f.cancels.end})
	if o.TokensPath != "" {
		f.tokens, f.tokErr = store.OpenTokens(o.TokensPath, store.TokensOptions{Now: o.TokensNow, OnEnd: f.cancels.end, Lifetimes: f.plan.Lifetimes})
		if f.tokErr != nil {
			f.log.Error("front: named tokens unavailable; every bearer token will be answered 503", "err", f.tokErr)
		}
	} else {
		f.tokErr = errors.New("this front has no tokens file")
	}
	if _, err := fs.Stat(orEmpty(o.UI), "index.html"); err != nil {
		f.noUI = true
		f.log.Warn("front: this binary was built without the dashboard UI (a plain `go build`); build with ./build-with-ui.sh")
	} else {
		f.ui = http.FileServer(http.FS(o.UI))
	}
	if f.plan.Fallback != "" {
		o.Audit.Event("fallback", slog.String("reason", f.plan.Fallback), slog.String("listen", f.plan.Listen),
			slog.String("refused", f.plan.Refused))
	}
	f.holdRefused()
	go f.sweep()
	return f
}

// Close stops the sweeper, closes every websocket and stream it holds
// (1001), stops answering 503 on Plan.Refused and releases the tokens
// file. It does not close the Plan (its TLS reloader) or the Audit.
func (f *Front) Close() {
	f.once.Do(func() {
		if f.refused != nil {
			f.refused.Close()
		}
		close(f.stop)
		<-f.done
		f.cancels.endAll()
		if f.tokens != nil {
			f.tokens.Close()
		}
		f.mu.Lock()
		for _, tr := range f.transports {
			tr.CloseIdleConnections()
		}
		f.mu.Unlock()
	})
}

func (f *Front) sweep() {
	defer close(f.done)
	t := time.NewTicker(f.o.Sweep)
	defer t.Stop()
	failing := false
	for {
		select {
		case <-f.stop:
			return
		case <-t.C:
		}
		f.sessions.Sweep()
		if f.tokens != nil {
			err := f.tokens.Sweep()
			if err != nil && !failing {
				f.log.Error("front: the tokens file cannot be read", "err", err)
			}
			failing = err != nil
		}
	}
}

// ServeHTTP is the door.
func (f *Front) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !cleanPath(r) {
		plainError(w, http.StatusBadRequest, "bad path")
		return
	}
	if !f.CheckHost(r) {
		msg := "this front does not answer to that Host"
		if f.plan.HostKnown {
			msg = "This front has no sign-in (auth: local, served beyond loopback by --insecure-open), so it answers only " +
				f.known() + " -- not another DNS name, which a page elsewhere could point at it; set url: to reach it" +
				" by that name, or choose auth: password or proxy"
		}
		plainError(w, http.StatusForbidden, msg)
		return
	}
	path := r.URL.Path
	rig, owned := f.o.Route(path)
	if !owned {
		if isAuthPath(path) {
			f.serveAuth(w, r, nil, path)
			return
		}
		if f.o.Fallback != nil {
			f.o.Fallback.ServeHTTP(w, r)
			return
		}
		http.NotFound(w, r)
		return
	}
	rel := strings.TrimPrefix(path, rig.Root)
	if rel == "" {
		rel = "/"
	}
	switch {
	case isAuthPath(rel):
		f.serveAuth(w, r, &rig, rel)
	case under(rel, "/api"), under(rel, "/ws"), under(rel, "/mcp"):
		f.serveProxy(w, r, rig)
	default:
		f.serveUI(w, r, rel)
	}
}

func isAuthPath(p string) bool { return under(p, "/api/auth") }

func under(p, prefix string) bool { return p == prefix || strings.HasPrefix(p, prefix+"/") }

// cleanPath refuses what a path-matching layer could be fooled by (Home
// Assistant CVE-2023-27482): `.` and `..` segments, a backslash, and the
// encoded `.`, `/` and `\` -- before any routing (merge requirement 22).
func cleanPath(r *http.Request) bool {
	raw := r.RequestURI
	if !strings.HasPrefix(raw, "/") {
		raw = r.URL.EscapedPath()
	}
	raw, _, _ = strings.Cut(raw, "?")
	low := strings.ToLower(raw)
	if strings.Contains(low, "%2e") || strings.Contains(low, "%2f") || strings.Contains(low, "%5c") || strings.ContainsRune(raw, '\\') {
		return false
	}
	for _, p := range []string{raw, r.URL.Path} {
		for _, seg := range strings.Split(p, "/") {
			if seg == "." || seg == ".." {
				return false
			}
		}
	}
	return strings.HasPrefix(r.URL.Path, "/")
}

// frontHeaders are on everything the front answers itself.
func frontHeaders(w http.ResponseWriter) {
	h := w.Header()
	h.Set("Content-Security-Policy", "frame-ancestors 'none'")
	h.Set("X-Frame-Options", "DENY")
	h.Set("X-Content-Type-Options", "nosniff")
}

func plainError(w http.ResponseWriter, code int, msg string) {
	frontHeaders(w)
	http.Error(w, msg, code)
}

func (f *Front) serveUI(w http.ResponseWriter, r *http.Request, rel string) {
	frontHeaders(w)
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if f.noUI {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusServiceUnavailable)
		fmt.Fprint(w, placeholderPage)
		return
	}
	r2 := r.Clone(r.Context())
	r2.URL.Path, r2.URL.RawPath = rel, ""
	f.ui.ServeHTTP(w, r2)
}

// placeholderPage is merge requirement 32: never the placeholder UI silently.
const placeholderPage = `<!doctype html>
<html><head><meta charset="utf-8"><title>flyball: no dashboard in this build</title></head>
<body style="font-family: sans-serif; max-width: 40em; margin: 3em auto">
<h1>This flyball binary has no dashboard</h1>
<p>It was built with a plain <code>go build</code>, which embeds a placeholder instead of the
dashboard UI. Build it with <code>./build-with-ui.sh</code> (in <code>daemon/</code>) to get a
binary that serves the dashboard.</p>
<p>The rig itself is running: its API under <code>/api</code> answers as usual.</p>
</body></html>
`

func orEmpty(fsys fs.FS) fs.FS {
	if fsys == nil {
		return emptyFS{}
	}
	return fsys
}

type emptyFS struct{}

func (emptyFS) Open(string) (fs.File, error) { return nil, fs.ErrNotExist }

func randomHex(n int) string {
	b := make([]byte, n)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// The server's limits (serve_ui.go's, kept; adv-blind 13). No ReadTimeout
// or WriteTimeout: a websocket or a long download is one request that
// lasts as long as it needs. Variables so a test can shorten them.
var (
	ReadHeaderTimeout = 10 * time.Second
	IdleTimeout       = 120 * time.Second
	MaxHeaderBytes    = 64 << 10
)

// NewServer is an http.Server for h with the front's limits and, when the
// plan has TLS, its reloading certificate.
func NewServer(p Plan, h http.Handler) *http.Server {
	srv := &http.Server{
		Addr:              p.Listen,
		Handler:           h,
		ReadHeaderTimeout: ReadHeaderTimeout,
		IdleTimeout:       IdleTimeout,
		MaxHeaderBytes:    MaxHeaderBytes,
	}
	if p.TLS != nil {
		srv.TLSConfig = p.TLS.Config()
	}
	return srv
}

// Listen opens the plan's listener: TCP, or `unix:/path` (a stale socket
// file nobody answers on is replaced), wrapped in TLS when the plan has it.
// A unix socket is made 0660, whatever the umask (on Linux before bind, so
// it is never connectable with a wider mode), and is refused in a
// directory another user could replace it in or, when the front owns the
// directory, reach it through: an unsigned proxy preset believes whoever
// connects.
func Listen(p Plan) (net.Listener, error) {
	var ln net.Listener
	var err error
	if path, ok := strings.CutPrefix(p.Listen, "unix:"); ok {
		if err := checkSocketDir(filepath.Dir(path)); err != nil {
			return nil, fmt.Errorf("listen %s: %w", p.Listen, err)
		}
		if fi, statErr := os.Lstat(path); statErr == nil && fi.Mode()&os.ModeSocket != 0 {
			if c, dialErr := net.DialTimeout("unix", path, time.Second); dialErr == nil {
				c.Close()
				return nil, fmt.Errorf("listen %s: another process answers on it", p.Listen)
			}
			os.Remove(path)
		}
		lc := net.ListenConfig{Control: socketControl} // the mode, before bind
		ln, err = lc.Listen(context.Background(), "unix", path)
		if err == nil {
			listenedHook(path)
			if err = os.Chmod(path, 0o660); err != nil {
				ln.Close()
			}
		}
	} else {
		ln, err = net.Listen("tcp", p.Listen)
	}
	if err != nil {
		return nil, err
	}
	if p.TLS != nil {
		ln = tls.NewListener(ln, p.TLS.Config())
	}
	return ln, nil
}

// listenedHook runs as soon as a unix socket listens, for a test.
var listenedHook = func(string) {}

// Serve listens on the plan's address and serves h until ctx is done, then
// shuts down (5 s grace).
func Serve(ctx context.Context, p Plan, h http.Handler) error {
	ln, err := Listen(p)
	if err != nil {
		return err
	}
	srv := NewServer(p, h)
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
