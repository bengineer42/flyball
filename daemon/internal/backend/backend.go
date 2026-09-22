// Package backend is the swappable layer plan.md's Sequencing section
// calls for: whatever actually starts a runner (raw exec.Command today,
// a Docker API client later, option 2) sits behind Backend, keyed by a
// full host:port endpoint, never just a port -- the hinge that keeps
// adding Docker support later additive, not a rewrite.
package backend

import "io"

type Status string

const (
	StatusStarting Status = "starting" // spawned, not yet answering /api/auth
	StatusRunning  Status = "running"
	StatusCrashed  Status = "crashed"
	StatusStopped  Status = "stopped"
)

type RunnerInfo struct {
	Name     string
	Endpoint string // host:port -- 127.0.0.1:8102 today, a container IP later
	Status   Status
}

// Backend is interface.md's internal sketch, implemented for real here.
type Backend interface {
	// Start spawns a runner and returns its endpoint. Does not block
	// until the runner answers /api/auth -- that's the registry's job
	// (plan.md's "knowing it actually started" polling). uvProject, when
	// non-empty, launches flyball-runner via `uv run --project uvProject`
	// instead of execing it bare (Manifest.UvProject).
	Start(name, serverConfig, host string, port int, rootPath, uvProject string) (endpoint string, err error)
	Stop(name string) error
	Restart(name string) error
	Logs(name string) (io.Reader, error)
	Status(name string) (Status, error)
}
