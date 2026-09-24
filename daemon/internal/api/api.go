// Package api is flyballd's HTTP interface, behind its front (package
// front): the rigs under their root paths, proxied by the front with a
// signed principal, and the daemon's own management routes -- the runner
// list, the landing page, start/stop/restart/logs -- which need a bearer
// token carrying the management scope (grants.Management(), made by
// `flyball token create`). A web session, the local shape's console or a
// proxy identity never has it (F21, merge requirement 18). One route is
// not management: GET /api/rigs, the rigs the caller holds a verb on.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"html"
	"io"
	"net/http"
	"sort"
	"strings"

	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/front"
	"flyballd/internal/grants"
	"flyballd/internal/registry"
)

// Authenticator runs the front's provider chain on a request (the
// *front.Front).
type Authenticator interface {
	Authenticate(r *http.Request) (front.Caller, error)
}

// Server is the management API and the landing page: the front's
// Fallback, reached only after the front's path and Host checks.
type Server struct {
	reg  *registry.Registry
	auth Authenticator
	mux  *http.ServeMux
}

// New is the management API over reg, authenticating with auth.
func New(reg *registry.Registry, auth Authenticator) *Server {
	s := &Server{reg: reg, auth: auth, mux: http.NewServeMux()}
	s.routes()
	return s
}

// NewFront is flyballd's front: o with Route over reg's runners and the
// management API as its Fallback.
func NewFront(reg *registry.Registry, o front.Options) *front.Front {
	s := &Server{reg: reg, mux: http.NewServeMux()}
	s.routes()
	o.Route = Route(reg)
	o.Fallback = s
	f := front.New(o)
	s.auth = f
	return f
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) { s.mux.ServeHTTP(w, r) }

func (s *Server) routes() {
	s.mux.HandleFunc("GET /api/runners", s.manage(s.handleListRunners))
	s.mux.HandleFunc("GET /api/runners/{name}", s.manage(s.handleGetRunner))
	s.mux.HandleFunc("POST /api/runners", s.manage(s.handleStartRunner))
	s.mux.HandleFunc("DELETE /api/runners/{name}", s.manage(s.handleStopRunner))
	s.mux.HandleFunc("POST /api/runners/{name}/restart", s.manage(s.handleRestartRunner))
	s.mux.HandleFunc("GET /api/runners/{name}/logs", s.manage(s.handleLogs))
	s.mux.HandleFunc("GET /{$}", s.manage(s.handleLanding))
	s.mux.HandleFunc("GET /api/rigs", s.handleRigs)
}

// manage gates a route on a bearer token with the management scope: a
// POST here starts a process from a caller-named config file. No
// credential (outside the local shape) is 401; a credential without the
// scope -- a session, the local console, a token without it -- is 403.
func (s *Server) manage(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		c, err := s.auth.Authenticate(r)
		if err != nil {
			w.Header().Set("WWW-Authenticate", `Bearer realm="flyballd"`)
			http.Error(w, err.Error(), front.Status(err))
			return
		}
		switch {
		case c.Scheme == front.SchemeToken && grants.HasManagement(c.Scopes):
			next(w, r)
		case c.Scheme == front.SchemeAnonymous:
			w.Header().Set("WWW-Authenticate", `Bearer realm="flyballd"`)
			http.Error(w, "flyballd's management API needs a bearer token with the "+grants.Management()+" scope", http.StatusUnauthorized)
		default:
			http.Error(w, "flyballd's management API needs a bearer token with the "+grants.Management()+
				" scope (`flyball token create --scope "+grants.Management()+"`); a sign-in never grants it", http.StatusForbidden)
		}
	}
}

// Route finds the runner that owns a path: the longest root path that is
// the path or a prefix of it by whole segments. The rig's scopes (Name)
// and the aud its principal is minted for (the channel's) come from that
// same registered entry: both are the manifest name (merge requirement 4).
// Registration refuses overlapping roots (registry.ErrConflict), so at
// most one can match; longest-first is belt and braces.
func Route(reg *registry.Registry) func(string) (front.Rig, bool) {
	return func(path string) (front.Rig, bool) {
		var best *registry.Entry
		for _, e := range reg.List() {
			root := e.Manifest.RootPath
			if (path == root || strings.HasPrefix(path, root+"/")) && (best == nil || len(root) > len(best.Manifest.RootPath)) {
				best = e
			}
		}
		if best == nil {
			return front.Rig{}, false
		}
		name := best.Manifest.Name
		return front.Rig{Root: best.Manifest.RootPath, Name: name, Target: func(context.Context) (front.Target, error) {
			e, ok := reg.Get(name)
			if !ok {
				return front.Target{}, front.ErrNotRunning
			}
			if err := front.StatusErr(e.Status); err != nil {
				return front.Target{}, err
			}
			ch, err := reg.Channel(name)
			if err != nil {
				return front.Target{}, front.ErrNotRunning
			}
			return front.TargetOf(ch)
		}}, true
	}
}

// verbs is c's verbs on rig: the front's own rule when auth is the front
// (a token's issuer's verbs intersected), else c's ceiling for rig.
func (s *Server) verbs(c front.Caller, rig string) []string {
	if v, ok := s.auth.(interface {
		Verbs(front.Caller, string) []string
	}); ok {
		return v.Verbs(c, rig)
	}
	return grants.ForRig(c.Scopes, rig)
}

// handleRigs is GET /api/rigs: the rigs the caller holds any verb on,
// sorted by name -- {name, root_path, status}. Not a management route:
// what it shows is what the caller could reach anyway, and `flyball stop
// --all` needs it with only operate (D-037). A credential that does not
// work is refused (401/503), as everywhere, and an anonymous caller by an
// unknown Host is 403 (D-043).
func (s *Server) handleRigs(w http.ResponseWriter, r *http.Request) {
	c, err := s.auth.Authenticate(r)
	if err != nil {
		w.Header().Set("WWW-Authenticate", `Bearer realm="flyballd"`)
		http.Error(w, err.Error(), front.Status(err))
		return
	}
	// D-043: an anonymous reader by a name a page elsewhere can own (DNS
	// rebinding) does not get the rig list; the front's rule, as its rigs'.
	if h, ok := s.auth.(interface {
		HostRefusal(front.Caller, *http.Request) string
	}); ok {
		if msg := h.HostRefusal(c, r); msg != "" {
			http.Error(w, msg, http.StatusForbidden)
			return
		}
	}
	out := []map[string]any{}
	entries := s.reg.List()
	sort.Slice(entries, func(i, j int) bool { return entries[i].Manifest.Name < entries[j].Manifest.Name })
	for _, e := range entries {
		if len(s.verbs(c, e.Manifest.Name)) == 0 {
			continue
		}
		out = append(out, map[string]any{"name": e.Manifest.Name, "root_path": e.Manifest.RootPath, "status": e.Status})
	}
	writeJSON(w, out)
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
	e, ok := s.reg.Get(r.PathValue("name"))
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
		"endpoint":  e.Endpoint,
		"pid":       e.Pid,     // the live process, 0 when none
		"adopted":   e.Adopted, // taken over from a previous flyballd, not spawned (D-037)
		"reason":    e.Reason,  // why it is busy or failed, "" when not known
	}
}

// handleStartRunner starts a runner from a manifest (narrow scope: one
// pointing at an already-uv-synced rig file).
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
		code := http.StatusInternalServerError
		if errors.Is(err, registry.ErrConflict) {
			code = http.StatusConflict
		}
		http.Error(w, err.Error(), code)
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
// plain chunked text, not SSE.
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
