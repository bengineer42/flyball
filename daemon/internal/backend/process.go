package backend

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

// ProcessBackend is the only backend (Docker/option 2 dropped, per Ben's
// word, 17 Sep) -- raw subprocesses, no isolation (the honest caveat
// already flagged in plan.md: less sandboxing than dev-serve's Docker
// containers). Spawns the real, literal `flyball-runner` CLI -- the
// Python side's own command, now correctly named after the daemon/runner
// rename (was `flyball-daemon`, collided with this daemon's own name).
type ProcessBackend struct {
	logDir string

	// command builds the process for one incarnation of a runner; a test
	// swaps it for a shell script. minBackoff is the first crash backoff.
	command    func(uvProject string, args []string) *exec.Cmd
	minBackoff time.Duration
	// stopTimeout is how long Stop waits after SIGTERM before SIGKILL.
	stopTimeout time.Duration
	// ready says whether a runner answers yet; probed every probeInterval
	// after each start until it does.
	ready         func(endpoint, rootPath string) bool
	probeInterval time.Duration

	mu      sync.Mutex
	runners map[string]*runnerProc
}

// runnerProc is one registered runner. Every field but the fixed ones
// (endpoint, logFile, uvProject, args, wake, done) is guarded by
// ProcessBackend.mu.
type runnerProc struct {
	cmd      *exec.Cmd // the current incarnation
	alive    bool      // cmd is running (not yet reaped by supervise)
	endpoint string
	rootPath string
	status   Status
	// reachedRunning: the current incarnation answered its probe, so the
	// crash backoff starts again from the bottom.
	reachedRunning bool
	logFile        *os.File
	restarts       int

	uvProject string
	args      []string
	// restartRequested is set by Restart: the next exit is a restart,
	// whatever its exit code, never a stop or a crash.
	restartRequested bool
	// stopping is set by Stop: the next exit is the end, no restart.
	stopping bool

	wake chan struct{} // Stop/Restart cut a backoff wait short
	done chan struct{} // closed when supervise() returns
}

func NewProcessBackend(logDir string) (*ProcessBackend, error) {
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, fmt.Errorf("creating log dir: %w", err)
	}
	return &ProcessBackend{
		logDir:        logDir,
		runners:       map[string]*runnerProc{},
		command:       runnerCommand,
		minBackoff:    time.Second,
		stopTimeout:   10 * time.Second,
		ready:         answersAuth,
		probeInterval: 500 * time.Millisecond,
	}, nil
}

// answersAuth: the runner answers GET /api/auth under its root path -- it
// only does so once started with --root-path, so an unprefixed probe
// would 404 against a root_path-aware runner.
func answersAuth(endpoint, rootPath string) bool {
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://" + endpoint + rootPath + "/api/auth")
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// runnerCommand is the real flyball-runner, bare or via `uv run --project`
// -- flyball-runner only exists inside an app's own uv-managed venv, never
// bare on flyballd's own $PATH (same fix as `flyball run`'s --uv flag).
func runnerCommand(uvProject string, args []string) *exec.Cmd {
	if uvProject != "" {
		uvArgs := append([]string{"run", "--project", uvProject, "flyball-runner"}, args...)
		return exec.Command("uv", uvArgs...)
	}
	return exec.Command("flyball-runner", args...)
}

func (b *ProcessBackend) Start(name, serverConfig, host string, port int, rootPath, uvProject string) (string, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, taken := b.runners[name]; taken {
		return "", fmt.Errorf("a runner named %q is already running", name)
	}

	logPath := filepath.Join(b.logDir, name+".log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return "", fmt.Errorf("opening log file for %s: %w", name, err)
	}

	args := []string{
		serverConfig,
		"--host", host,
		"--port", fmt.Sprintf("%d", port),
	}
	if rootPath != "" {
		// Required for the daemon's convenience routing (api.go's
		// handleLandingOrProxy) to work: the runner needs to know its
		// own root_path to recognise the full, un-stripped prefixed path
		// the proxy forwards -- plan.md's Local UI routing section.
		args = append(args, "--root-path", rootPath)
	}
	cmd := b.command(uvProject, args)
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	// Deliberately NOT setting a process-group death-of-parent signal --
	// plan.md's crash-survival requirement: a daemon crash must not kill
	// its runners. Default Unix reparenting-on-parent-death is what we
	// want, not something to override.

	if err := cmd.Start(); err != nil {
		logFile.Close()
		return "", fmt.Errorf("starting runner %s: %w", name, err)
	}

	endpoint := fmt.Sprintf("%s:%d", host, port)
	rp := &runnerProc{cmd: cmd, alive: true, endpoint: endpoint, rootPath: rootPath, status: StatusStarting, logFile: logFile,
		uvProject: uvProject, args: args, wake: make(chan struct{}, 1), done: make(chan struct{})}
	b.runners[name] = rp

	go b.supervise(rp, cmd)
	go b.probe(rp, cmd)

	return endpoint, nil
}

// supervise waits on the process and restarts it: at once after a
// Restart, whatever the exit code, and with backoff after a crash, so a
// runner that dies immediately every time is not hot-looped. A clean exit
// nobody asked for is a stop.
func (b *ProcessBackend) supervise(rp *runnerProc, cmd *exec.Cmd) {
	defer close(rp.done)
	backoff := b.minBackoff
	for {
		err := cmd.Wait()
		b.mu.Lock()
		rp.alive = false
		if rp.stopping {
			rp.status = StatusStopped
			b.mu.Unlock()
			return
		}
		restart := rp.restartRequested
		rp.restartRequested = false
		if !restart && err == nil {
			rp.status = StatusStopped
			b.mu.Unlock()
			return
		}
		rp.restarts++
		if !restart {
			rp.status = StatusRestarting
		}
		if rp.reachedRunning {
			backoff = b.minBackoff
		}
		rp.reachedRunning = false
		b.mu.Unlock()

		// A requested restart goes straight back up; only a crash waits,
		// and Stop or Restart cut the wait short.
		if !restart {
			select {
			case <-time.After(backoff):
			case <-rp.wake:
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}
		}

		b.mu.Lock()
		if rp.stopping {
			rp.status = StatusStopped
			b.mu.Unlock()
			return
		}
		rp.restartRequested = false
		cmd = b.command(rp.uvProject, rp.args)
		cmd.Stdout = rp.logFile
		cmd.Stderr = rp.logFile
		if err := cmd.Start(); err != nil {
			fmt.Fprintf(rp.logFile, "flyballd: restarting: %v\n", err)
			rp.status = StatusFailed
			b.mu.Unlock()
			return
		}
		rp.cmd = cmd
		rp.alive = true
		rp.status = StatusStarting
		b.mu.Unlock()
		go b.probe(rp, cmd)
	}
}

// probe marks one incarnation running once it answers, and gives up when
// that incarnation is no longer the live one.
func (b *ProcessBackend) probe(rp *runnerProc, cmd *exec.Cmd) {
	for {
		b.mu.Lock()
		current := rp.cmd == cmd && rp.alive
		b.mu.Unlock()
		if !current {
			return
		}
		if b.ready(rp.endpoint, rp.rootPath) {
			b.mu.Lock()
			if rp.cmd == cmd && rp.alive && rp.status == StatusStarting {
				rp.status = StatusRunning
				rp.reachedRunning = true
			}
			b.mu.Unlock()
			return
		}
		time.Sleep(b.probeInterval)
	}
}

// Stop and Restart send SIGTERM first -- the daemon ends a runner's life
// with a signal it can catch and shut down cleanly on, rather than routing
// through the runner's own HTTP shutdown API. Keeps `allow_shutdown` off
// for daemon-managed runners with no conflict between two controllers of
// the same lifecycle.

// Stop deregisters a runner and ends it: SIGTERM, then SIGKILL if it is
// still there after stopTimeout. It returns once the process is gone and
// supervise() has finished, so nothing is left behind -- also when the
// runner was waiting out a crash backoff.
func (b *ProcessBackend) Stop(name string) error {
	b.mu.Lock()
	rp, ok := b.runners[name]
	if !ok {
		b.mu.Unlock()
		return fmt.Errorf("no runner named %q", name)
	}
	delete(b.runners, name)
	rp.stopping = true
	if rp.alive {
		rp.cmd.Process.Signal(syscall.SIGTERM)
	}
	b.nudge(rp)
	b.mu.Unlock()

	select {
	case <-rp.done:
	case <-time.After(b.stopTimeout):
		b.mu.Lock()
		if rp.alive {
			rp.cmd.Process.Kill()
		}
		b.mu.Unlock()
		<-rp.done
	}
	rp.logFile.Close()
	return nil
}

// Restart ends the runner's current process and starts a new one at once.
// The intent is recorded, not inferred from the exit code: a runner that
// exits 0 on SIGTERM is restarted all the same. A runner in crash backoff
// restarts now.
func (b *ProcessBackend) Restart(name string) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok {
		return fmt.Errorf("no runner named %q", name)
	}
	rp.restartRequested = true
	if rp.alive {
		return rp.cmd.Process.Signal(syscall.SIGTERM) // supervise() restarts it
	}
	b.nudge(rp)
	return nil
}

// nudge wakes supervise() from a backoff wait, if it is in one.
func (b *ProcessBackend) nudge(rp *runnerProc) {
	select {
	case rp.wake <- struct{}{}:
	default:
	}
}

func (b *ProcessBackend) Logs(name string) (io.Reader, error) {
	path := filepath.Join(b.logDir, name+".log")
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("opening log for %s: %w", name, err)
	}
	return f, nil
}

func (b *ProcessBackend) Status(name string) (Status, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok {
		return "", fmt.Errorf("no runner named %q", name)
	}
	return rp.status, nil
}
