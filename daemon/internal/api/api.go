// Package api is the daemon's own external interface, per
// brain/plans/rig-deployment/interface.md's "External: the daemon's own
// HTTP API" table. Registration-only actions (start/stop/restart/logs)
// are gated by daemon auth; GET /api/runners is open, per plan.md's
// "reading the list is fine open" note. Optional convenience routing
// (/{name}/*, pass-through to a runner) is also here, per the
// Architecture revision -- no longer the daemon's core job, but kept as
// a mode, per plan.md's still-open question on whether to split it out.
package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"

	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/registry"
)

type Server struct {
	reg    *registry.Registry
	daemon config.DaemonConfig
	mux    *http.ServeMux
}

func New(reg *registry.Registry, daemon config.DaemonConfig) *Server {
	s := &Server{reg: reg, daemon: daemon, mux: http.NewServeMux()}
	s.routes()
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) { s.mux.ServeHTTP(w, r) }

func (s *Server) routes() {
	s.mux.HandleFunc("GET /api/auth", s.handleAuth)
	s.mux.HandleFunc("GET /api/runners", s.handleListRunners)
	s.mux.HandleFunc("GET /api/runners/{name}", s.handleGetRunner)
	s.mux.HandleFunc("POST /api/runners", s.requireAuth(s.handleStartRunner))
	s.mux.HandleFunc("DELETE /api/runners/{name}", s.requireAuth(s.handleStopRunner))
	s.mux.HandleFunc("POST /api/runners/{name}/restart", s.requireAuth(s.handleRestartRunner))
	s.mux.HandleFunc("GET /api/runners/{name}/logs", s.requireAuth(s.handleLogs))
	s.mux.HandleFunc("/", s.handleLandingOrProxy)
}

// requireAuth is a stub matching interface.md's table -- daemon auth
// design is still an open question in plan.md ("Daemon's own auth --
// needs its own design, not inherited"). Currently a no-op placeholder,
// deliberately not wired to anything real yet, so it isn't mistaken for
// a finished security boundary.
func (s *Server) requireAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.daemon.Auth.Password == "" {
			next(w, r)
			return
		}
		// TODO: real session/token check once daemon auth is designed
		// (plan.md, "Daemon's own auth" open question).
		next(w, r)
	}
}

func (s *Server) handleAuth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, map[string]any{
		"scheme":   "anonymous",
		"level":    "operate",
		"password": s.daemon.Auth.Password != "",
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
	if m.Name == "" || m.ServerConfig == "" || m.Port == 0 {
		http.Error(w, "name, server_config and port are required", http.StatusBadRequest)
		return
	}
	if m.Host == "" {
		m.Host = "127.0.0.1"
	}
	if m.RootPath == "" {
		m.RootPath = "/" + m.Name
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
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	io.Copy(w, rd)
}

// handleLandingOrProxy: "/" is the landing page; "/{name}/*" is optional
// convenience pass-through routing to that runner, forwarding the FULL
// prefixed path unchanged -- the bug dev-serve/proxy.py hit and fixed
// this session (a runner expects its root_path kept, not stripped).
func (s *Server) handleLandingOrProxy(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/" {
		s.handleLanding(w, r)
		return
	}
	for _, e := range s.reg.List() {
		prefix := e.Manifest.RootPath
		if r.URL.Path == prefix || strings.HasPrefix(r.URL.Path, prefix+"/") {
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
	entries := s.reg.List()
	var b strings.Builder
	b.WriteString("<!doctype html><html><head><title>flyball</title></head><body><h1>flyball</h1><ul>\n")
	for _, e := range entries {
		b.WriteString("<li><a href=\"" + e.Manifest.RootPath + "/\">" + e.Manifest.RootPath + "/</a></li>\n")
	}
	b.WriteString("</ul></body></html>")
	w.Header().Set("Content-Type", "text/html")
	io.WriteString(w, b.String())
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
