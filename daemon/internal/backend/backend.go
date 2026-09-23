// Package backend is the swappable layer plan.md's Sequencing section
// calls for: whatever actually starts a runner (raw exec.Command today,
// a Docker API client later, option 2) sits behind Backend, keyed by a
// full host:port endpoint, never just a port -- the hinge that keeps
// adding Docker support later additive, not a rewrite.
package backend

import "io"

type Status string

// A runner's status follows its process: starting until it answers
// /api/auth, running, restarting while it waits out a crash backoff,
// stopped once it has exited cleanly or been stopped, failed once it has
// crashed and will not be restarted.
const (
	StatusStarting   Status = "starting"
	StatusRunning    Status = "running"
	StatusRestarting Status = "restarting"
	StatusStopped    Status = "stopped"
	StatusFailed     Status = "failed"
)

// Restart policies, from a manifest's `restart:`. Stop and Restart
// requests override them.
const (
	RestartOnFailure = "on-failure" // the default: restart after a crash, not after a clean exit
	RestartAlways    = "always"     // restart after any exit
	RestartNever     = "never"      // never restart: a crash leaves it failed
)

// Spec is what a runner is started from.
type Spec struct {
	ServerConfig string // the rig file
	Host         string
	Port         int
	RootPath     string
	// UvProject, when non-empty, launches flyball-runner via `uv run
	// --project UvProject` instead of execing it bare.
	UvProject string
	Restart   string // a Restart* policy; empty is on-failure
}

type RunnerInfo struct {
	Name     string
	Endpoint string // host:port -- 127.0.0.1:8102 today, a container IP later
	Status   Status
}

// Backend is interface.md's internal sketch, implemented for real here.
type Backend interface {
	// Start spawns a runner and returns its endpoint. It does not block
	// until the runner answers /api/auth; Status says starting until then.
	Start(name string, spec Spec) (endpoint string, err error)
	Stop(name string) error
	Restart(name string) error
	Logs(name string) (io.ReadCloser, error)
	Status(name string) (Status, error)
}
