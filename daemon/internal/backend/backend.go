// Package backend is the swappable layer plan.md's Sequencing section
// calls for: whatever actually starts a runner (raw exec.Command today,
// a Docker API client later, option 2) sits behind Backend, keyed by a
// full endpoint (endpoint.Endpoint: a unix socket or loopback TCP),
// never just a port -- the hinge that keeps adding Docker support later
// additive, not a rewrite.
//
// Every runner a backend starts is fronted: it is handed a front-dir
// (`--front-dir DIR`, see package frontdir) holding a fresh key, its aud
// and its endpoint, and it binds only that endpoint and accepts only
// principals signed with that key.
package backend

import (
	"io"

	"flyballd/internal/endpoint"
)

type Status string

// A runner's status follows its process: starting until it passes the
// readiness handshake (endpoint.Handshake), running, restarting while it
// waits out a crash backoff, stopped once it has exited cleanly or been
// stopped, failed once it has crashed and will not be restarted, busy
// when another runner already has its rig (exit 3) or its front-dir
// (runner.lock held).
const (
	StatusStarting   Status = "starting"
	StatusRunning    Status = "running"
	StatusRestarting Status = "restarting"
	StatusStopped    Status = "stopped"
	StatusFailed     Status = "failed"
	StatusBusy       Status = "busy"
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
	// Network: "unix" (a socket in the front-dir) or "tcp" (Host:Port on
	// loopback). "" is unix, or tcp on Windows.
	Network  string
	Host     string // tcp only; "" is 127.0.0.1
	Port     int    // tcp only, and required there
	RootPath string
	// Aud is the audience the runner verifies principals against; "" is
	// the runner's name (flyballd's rule). `flyball run` passes run-<8 hex>.
	Aud string
	// FrontDir, when set, is the runner's front-dir; "" makes one under
	// the backend's FrontOptions.Root, or a temp dir.
	FrontDir string
	// Env is added to the inherited environment of every incarnation,
	// respawns included. Never a credential: the key travels in the
	// front-dir only.
	Env []string
	// UvProject, when non-empty, launches flyball-runner via `uv run
	// --project UvProject` instead of execing it bare.
	UvProject string
	Restart   string // a Restart* policy; empty is on-failure
}

type RunnerInfo struct {
	Name     string
	Endpoint endpoint.Endpoint
	Status   Status
}

// Backend is interface.md's internal sketch, implemented for real here.
type Backend interface {
	// Start spawns a runner and returns its endpoint's string form
	// (endpoint.Endpoint.String()). It does not block until the runner
	// passes the readiness handshake; Status says starting until then.
	Start(name string, spec Spec) (endpoint string, err error)
	Stop(name string) error
	Restart(name string) error
	Logs(name string) (io.ReadCloser, error)
	Status(name string) (Status, error)
}

// Channel is what a front needs to reach one runner: where it listens,
// its front-dir, the aud it verifies, and the key of its current
// incarnation (zero while none has been spawned, e.g. busy). The key
// changes at every spawn, so ask again rather than keep it.
type Channel struct {
	Endpoint endpoint.Endpoint
	Dir      string
	Aud      string
	Key      [32]byte
}

// Fronted is a Backend that can hand a front its runners' channels.
// ProcessBackend is one.
type Fronted interface {
	Channel(name string) (Channel, error)
}
