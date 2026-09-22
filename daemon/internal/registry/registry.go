// Package registry is the daemon's live view of what's running --
// interface.md's GET /api/runners table, and the thing routing/health
// checks read from. Built from what the daemon actually spawned, per
// plan.md, not hand-edited (dev-serve/sites.json's job today, done for
// real here).
package registry

import (
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"flyballd/internal/backend"
	"flyballd/internal/config"
)

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

// Start spawns a runner via the backend and begins polling its /api/auth
// until it answers -- plan.md's "knowing it actually started" section:
// a process that's running but not yet answering shouldn't be routable
// yet, so it's marked StatusStarting until the poll succeeds.
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
	endpoint, err := r.be.Start(m.Name, m.ServerConfig, m.Host, m.Port, m.RootPath, m.UvProject)
	if err != nil {
		return err
	}
	r.mu.Lock()
	r.entries[m.Name] = &Entry{Manifest: m, Endpoint: endpoint, Status: backend.StatusStarting}
	r.mu.Unlock()

	go r.pollUntilUp(m.Name, endpoint, m.RootPath)
	return nil
}

func (r *Registry) pollUntilUp(name, endpoint, rootPath string) {
	client := &http.Client{Timeout: 2 * time.Second}
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		// The runner only answers under its own root_path once started
		// with --root-path (backend.ProcessBackend.Start) -- an unprefixed
		// /api/auth 404s against a root_path-aware runner.
		resp, err := client.Get("http://" + endpoint + rootPath + "/api/auth")
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				r.mu.Lock()
				if e, ok := r.entries[name]; ok {
					e.Status = backend.StatusRunning
				}
				r.mu.Unlock()
				if pb, ok := r.be.(*backend.ProcessBackend); ok {
					pb.MarkRunning(name)
				}
				return
			}
		}
		time.Sleep(500 * time.Millisecond)
	}
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

// Logs reaches the backend's own Logs(name), per interface.md's Backend
// abstraction table -- the registry is the only thing above Backend that
// the API layer talks to, so it needs a narrow accessor rather than
// exposing the whole backend.Backend.
func (r *Registry) Logs(name string) (io.Reader, error) {
	return r.be.Logs(name)
}

func (r *Registry) Get(name string) (*Entry, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	e, ok := r.entries[name]
	return e, ok
}

func (r *Registry) List() []*Entry {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]*Entry, 0, len(r.entries))
	for _, e := range r.entries {
		out = append(out, e)
	}
	return out
}
