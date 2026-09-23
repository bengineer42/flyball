// Package api is the daemon's own external interface, per
// brain/plans/rig-deployment/interface.md's "External: the daemon's own
// HTTP API" table. Every route of its own but GET /api/auth -- the runner
// list, the landing page, start/stop/restart/logs -- needs the daemon's
// bearer token; the per-runner proxy does not (the runner has its own
// door). Optional convenience routing
// (/{name}/*, pass-through to a runner) is also here, per the
// Architecture revision -- no longer the daemon's core job, but kept as
// a mode, per plan.md's still-open question on whether to split it out.
package api

import (
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"

	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/exposure"
	"flyballd/internal/registry"
)

type Server struct {
	reg    *registry.Registry
	daemon config.DaemonConfig
	mux    *http.ServeMux

	probe   *http.Client
	doorsMu sync.Mutex
	doors   map[string]time.Time // /api/auth URL -> when it last said it had a door
}

func New(reg *registry.Registry, daemon config.DaemonConfig) *Server {
	s := &Server{
		reg: reg, daemon: daemon, mux: http.NewServeMux(),
		probe: &http.Client{Timeout: 2 * time.Second}, doors: map[string]time.Time{},
	}
	s.routes()
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) { s.mux.ServeHTTP(w, r) }

func (s *Server) routes() {
	s.mux.HandleFunc("GET /api/auth", s.handleAuth)
	s.mux.HandleFunc("GET /api/runners", s.requireAuth(s.handleListRunners))
	s.mux.HandleFunc("GET /api/runners/{name}", s.requireAuth(s.handleGetRunner))
	s.mux.HandleFunc("POST /api/runners", s.requireAuth(s.handleStartRunner))
	s.mux.HandleFunc("DELETE /api/runners/{name}", s.requireAuth(s.handleStopRunner))
	s.mux.HandleFunc("POST /api/runners/{name}/restart", s.requireAuth(s.handleRestartRunner))
	s.mux.HandleFunc("GET /api/runners/{name}/logs", s.requireAuth(s.handleLogs))
	s.mux.HandleFunc("/", s.handleLandingOrProxy)
}

// requireAuth gates a route on the daemon's bearer token. No token
// configured means the route is unavailable, not open: a POST here starts
// a process from a caller-named config file, so the default has to be
// closed. A richer scheme (plan.md's "Daemon's own auth" question) can
// replace this without moving the routes.
func (s *Server) requireAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.daemon.Auth.Token == "" {
			http.Error(w, "flyballd has no auth.token: set one in its config to list, start, stop, restart or read runners over the API",
				http.StatusServiceUnavailable)
			return
		}
		if !s.bearerOK(r) {
			w.Header().Set("WWW-Authenticate", `Bearer realm="flyballd"`)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next(w, r)
	}
}

func (s *Server) bearerOK(r *http.Request) bool {
	got, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	return ok && subtle.ConstantTimeCompare([]byte(got), []byte(s.daemon.Auth.Token)) == 1
}

func (s *Server) handleAuth(w http.ResponseWriter, r *http.Request) {
	scheme, level := "anonymous", "read"
	if s.daemon.Auth.Token != "" && s.bearerOK(r) {
		scheme, level = "token", "operate"
	}
	writeJSON(w, map[string]any{
		"scheme": scheme,
		"level":  level,
		"token":  s.daemon.Auth.Token != "",
	})
}

func (s *Server) handleListRunners(w http.ResponseWriter, r *http.Request) {
	entries := s.reg.List()
	out := make([]map[string]any, 0, len(entries))
	for _, e := range entries {
		out = append(out, runnerJSON(e))
	}
	writeJSON(w, out)
}

func (s *Server) handleGetRunner(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	e, ok := s.reg.Get(name)
	if !ok {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, runnerJSON(e))
}

func runnerJSON(e *registry.Entry) map[string]any {
	return map[string]any{
		"name":      e.Manifest.Name,
		"root_path": e.Manifest.RootPath,
		"restart":   e.Manifest.Restart,
		"status":    e.Status,
	}
}

// handleStartRunner's body shape is the open question interface.md
// flags: this implements narrow scope only (a manifest pointing at an
// already-uv-synced server config). Full-scope provisioning (fetch/build
// a rig's environment) is not implemented -- plan.md's Provisioning
// question is still open.
func (s *Server) handleStartRunner(w http.ResponseWriter, r *http.Request) {
	var m config.Manifest
	if err := json.NewDecoder(r.Body).Decode(&m); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if m.Host == "" {
		m.Host = "127.0.0.1"
	}
	if m.RootPath == "" {
		m.RootPath = "/" + m.Name
	}
	if err := m.Validate(); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if err := s.reg.Start(m); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusAccepted)
	writeJSON(w, map[string]any{"name": m.Name, "status": backend.StatusStarting})
}

func (s *Server) handleStopRunner(w http.ResponseWriter, r *http.Request) {
	if err := s.reg.Stop(r.PathValue("name")); err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleRestartRunner(w http.ResponseWriter, r *http.Request) {
	if err := s.reg.Restart(r.PathValue("name")); err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// handleLogs streams the runner's captured log file straight through --
// plain chunked text, per interface.md's "leaning simplest" note, not
// SSE. A first pass: no --follow/tail semantics, just whatever the
// backend's Logs(name) reader currently holds.
func (s *Server) handleLogs(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if _, ok := s.reg.Get(name); !ok {
		http.NotFound(w, r)
		return
	}
	rd, err := s.reg.Logs(name)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	defer rd.Close()
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	io.Copy(w, rd)
}

// handleLandingOrProxy: "/" is the landing page; "/{name}/*" is optional
// convenience pass-through routing to that runner, forwarding the FULL
// prefixed path unchanged -- the bug dev-serve/proxy.py hit and fixed
// this session (a runner expects its root_path kept, not stripped).
// Beyond loopback an open runner is not proxied to (guard).
func (s *Server) handleLandingOrProxy(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/" {
		s.requireAuth(s.handleLanding)(w, r)
		return
	}
	for _, e := range s.reg.List() {
		prefix := e.Manifest.RootPath
		if r.URL.Path == prefix || strings.HasPrefix(r.URL.Path, prefix+"/") {
			if err := s.guard(r, e); err != nil {
				http.Error(w, err.Error(), http.StatusServiceUnavailable)
				return
			}
			target, err := url.Parse("http://" + e.Endpoint)
			if err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			proxy := httputil.NewSingleHostReverseProxy(target)
			// Full path kept as-is: NewSingleHostReverseProxy already
			// preserves r.URL.Path unless a Director rewrites it, which
			// this doesn't -- deliberately, matching the fix from today.
			proxy.ServeHTTP(w, r)
			return
		}
	}
	http.NotFound(w, r)
}

func (s *Server) handleLanding(w http.ResponseWriter, r *http.Request) {
	var rootPaths []string
	for _, e := range s.reg.List() {
		rootPaths = append(rootPaths, e.Manifest.RootPath)
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	io.WriteString(w, renderLanding(rootPaths))
}

func renderLanding(rootPaths []string) string {
	var b strings.Builder
	b.WriteString("<!doctype html><html><head><title>flyball</title></head><body><h1>flyball</h1><ul>\n")
	for _, rp := range rootPaths {
		rp = html.EscapeString(rp)
		b.WriteString("<li><a href=\"" + rp + "/\">" + rp + "/</a></li>\n")
	}
	b.WriteString("</ul></body></html>")
	return b.String()
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

// StartupWarnings is what flyballd logs at start about how it is exposed:
// plain HTTP beyond loopback carries its token and every proxied runner's
// password, token and cookies in the clear; insecure_open proxies open
// runners to the network.
func StartupWarnings(cfg config.DaemonConfig) []string {
	if exposure.IsLoopback(cfg.Listen) {
		return nil
	}
	warnings := []string{exposure.CleartextWarning(cfg.Listen)}
	if cfg.Auth.InsecureOpen {
		warnings = append(warnings, fmt.Sprintf("auth.insecure_open: runners with no password and no token are proxied on %s, open to anyone who can reach it", cfg.Listen))
	}
	return warnings
}

// guard decides whether a request may be proxied to e: always when
// flyballd listens on loopback or auth.insecure_open is set; otherwise
// only when the runner's GET /api/auth says it has a password or a
// token. A runner that says it has one is believed for doorTTL.
func (s *Server) guard(r *http.Request, e *registry.Entry) error {
	if exposure.IsLoopback(s.daemon.Listen) || s.daemon.Auth.InsecureOpen {
		return nil
	}
	url := "http://" + e.Endpoint + e.Manifest.RootPath + "/api/auth"
	s.doorsMu.Lock()
	checked, ok := s.doors[url]
	s.doorsMu.Unlock()
	if ok && time.Since(checked) < doorTTL {
		return nil
	}
	door, err := exposure.Probe(r.Context(), s.probe, url)
	if err != nil {
		return fmt.Errorf("flyballd listens on %s beyond loopback and cannot tell whether runner %q has a password or a token (%v): not proxying to it", s.daemon.Listen, e.Manifest.Name, err)
	}
	if door.Open() {
		return fmt.Errorf("runner %q is open (no password, no token) and flyballd listens on %s beyond loopback: not proxying to it. Give the runner a password or a token (runner.auth, FLYBALL_PASSWORD, FLYBALL_TOKEN), listen on 127.0.0.1, or set auth.insecure_open in flyballd's config", e.Manifest.Name, s.daemon.Listen)
	}
	s.doorsMu.Lock()
	s.doors[url] = time.Now()
	s.doorsMu.Unlock()
	return nil
}

// doorTTL is how long a runner's door, once seen shut, is not asked again.
const doorTTL = 2 * time.Second
