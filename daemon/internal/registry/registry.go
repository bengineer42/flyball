// Package registry is the daemon's live view of what's running --
// interface.md's GET /api/runners table, and the thing routing/health
// checks read from. Built from what the daemon actually spawned, per
// plan.md, not hand-edited (dev-serve/sites.json's job today, done for
// real here).
package registry

import (
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"

	"flyballd/internal/backend"
	"flyballd/internal/config"
)

// Entry is one registered runner. Status is read from the backend each
// time an entry is fetched -- it follows the process, it is not kept here.
// Endpoint is the backend's string form of where the runner listens
// ("unix:/run/flyball/oven/sock", "tcp:127.0.0.1:8102"; endpoint.Parse
// reads it) -- what GET /api/runners reports.
type Entry struct {
	Manifest config.Manifest
	Endpoint string
	Status   backend.Status
}

type Registry struct {
	be backend.Backend

	mu      sync.RWMutex
	entries map[string]*Entry
	pending map[string]string // name -> root path, for Starts under way
}

func New(be backend.Backend) *Registry {
	return &Registry{be: be, entries: map[string]*Entry{}, pending: map[string]string{}}
}

// ErrConflict is a runner whose name is taken, or whose root path overlaps
// another's -- one contains the other, so a path could route to either and
// the aud a front mints for would not be the rig the scopes named (F6) --
// or the daemon's own /api.
var ErrConflict = errors.New("conflicts with a registered runner")

// ReservedRoot is the daemon's own: its management API and /api/auth.
const ReservedRoot = "/api"

func overlaps(a, b string) bool {
	return a == b || strings.HasPrefix(a, b+"/") || strings.HasPrefix(b, a+"/")
}

// reserve claims m's name and root path for a Start, or says why not.
func (r *Registry) reserve(m config.Manifest) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, taken := r.entries[m.Name]; taken {
		return fmt.Errorf("%w: a runner named %q is already registered", ErrConflict, m.Name)
	}
	if _, taken := r.pending[m.Name]; taken {
		return fmt.Errorf("%w: a runner named %q is already starting", ErrConflict, m.Name)
	}
	if overlaps(m.RootPath, ReservedRoot) {
		return fmt.Errorf("%w: root path %s is under flyballd's own %s", ErrConflict, m.RootPath, ReservedRoot)
	}
	roots := map[string]string{}
	for name, e := range r.entries {
		roots[name] = e.Manifest.RootPath
	}
	for name, root := range r.pending {
		roots[name] = root
	}
	for name, root := range roots {
		if overlaps(m.RootPath, root) {
			return fmt.Errorf("%w: root path %s overlaps runner %q's %s", ErrConflict, m.RootPath, name, root)
		}
	}
	r.pending[m.Name] = m.RootPath
	return nil
}

// Start spawns a runner via the backend. A name that is taken, or a root
// path that overlaps another runner's or /api, is ErrConflict. The backend reports it starting
// until it passes the readiness handshake, so a process that is up but
// not yet serving is not shown as running. The runner's aud is the
// manifest's name.
func (r *Registry) Start(m config.Manifest) error {
	if err := m.Validate(); err != nil {
		return err
	}
	if err := r.reserve(m); err != nil {
		return err
	}
	defer func() {
		r.mu.Lock()
		delete(r.pending, m.Name)
		r.mu.Unlock()
	}()
	endpoint, err := r.be.Start(m.Name, backend.Spec{
		ServerConfig: m.ServerConfig, Network: m.ResolvedNetwork(), Host: m.Host, Port: m.Port,
		RootPath: m.RootPath, Aud: m.Name, UvProject: m.UvProject, Restart: m.Restart,
	})
	if err != nil {
		return err
	}
	r.mu.Lock()
	r.entries[m.Name] = &Entry{Manifest: m, Endpoint: endpoint}
	r.mu.Unlock()
	return nil
}

func (r *Registry) Stop(name string) error {
	if err := r.be.Stop(name); err != nil {
		return err
	}
	r.mu.Lock()
	delete(r.entries, name)
	r.mu.Unlock()
	return nil
}

func (r *Registry) Restart(name string) error {
	return r.be.Restart(name)
}

// Logs reaches the backend's own Logs(name) -- the registry is the only
// thing above Backend that the API layer talks to, so it needs a narrow
// accessor rather than exposing the whole backend.Backend.
func (r *Registry) Logs(name string) (io.ReadCloser, error) {
	return r.be.Logs(name)
}

// Channel is what a front needs to reach runner name: its endpoint, aud
// and current key. It comes from the same registered entry the front
// routes by, so aud and route cannot disagree. An error if name is not
// registered or the backend is not backend.Fronted.
func (r *Registry) Channel(name string) (backend.Channel, error) {
	r.mu.RLock()
	_, ok := r.entries[name]
	r.mu.RUnlock()
	if !ok {
		return backend.Channel{}, fmt.Errorf("no runner named %q", name)
	}
	f, ok := r.be.(backend.Fronted)
	if !ok {
		return backend.Channel{}, fmt.Errorf("runner %q: the backend hands out no channels", name)
	}
	return f.Channel(name)
}

// Get returns a copy of the entry, its status read from the backend.
func (r *Registry) Get(name string) (*Entry, bool) {
	r.mu.RLock()
	e, ok := r.entries[name]
	r.mu.RUnlock()
	if !ok {
		return nil, false
	}
	return r.withStatus(e), true
}

// List returns copies of every entry, each with its current status.
func (r *Registry) List() []*Entry {
	r.mu.RLock()
	entries := make([]*Entry, 0, len(r.entries))
	for _, e := range r.entries {
		entries = append(entries, e)
	}
	r.mu.RUnlock()
	out := make([]*Entry, len(entries))
	for i, e := range entries {
		out[i] = r.withStatus(e)
	}
	return out
}

func (r *Registry) withStatus(e *Entry) *Entry {
	c := *e
	st, err := r.be.Status(e.Manifest.Name)
	if err != nil {
		st = backend.StatusStopped
	}
	c.Status = st
	return &c
}
