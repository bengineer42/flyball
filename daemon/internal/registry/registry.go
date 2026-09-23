// Package registry is the daemon's live view of what's running --
// interface.md's GET /api/runners table, and the thing routing/health
// checks read from. Built from what the daemon actually spawned, per
// plan.md, not hand-edited (dev-serve/sites.json's job today, done for
// real here).
package registry

import (
	"fmt"
	"io"
	"sync"

	"flyballd/internal/backend"
	"flyballd/internal/config"
)

// Entry is one registered runner. Status is read from the backend each
// time an entry is fetched -- it follows the process, it is not kept here.
type Entry struct {
	Manifest config.Manifest
	Endpoint string
	Status   backend.Status
}

type Registry struct {
	be backend.Backend

	mu      sync.RWMutex
	entries map[string]*Entry
}

func New(be backend.Backend) *Registry {
	return &Registry{be: be, entries: map[string]*Entry{}}
}

// Start spawns a runner via the backend. The backend reports it starting
// until it answers /api/auth, so a process that is up but not yet serving
// is not shown as running.
func (r *Registry) Start(m config.Manifest) error {
	if err := m.Validate(); err != nil {
		return err
	}
	r.mu.RLock()
	_, taken := r.entries[m.Name]
	r.mu.RUnlock()
	if taken {
		return fmt.Errorf("a runner named %q is already registered", m.Name)
	}
	endpoint, err := r.be.Start(m.Name, backend.Spec{
		ServerConfig: m.ServerConfig, Host: m.Host, Port: m.Port,
		RootPath: m.RootPath, UvProject: m.UvProject, Restart: m.Restart,
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
