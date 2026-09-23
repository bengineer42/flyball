package backend

import (
	"fmt"
	"io"
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

	mu      sync.Mutex
	runners map[string]*runnerProc
}

type runnerProc struct {
	cmd      *exec.Cmd
	endpoint string
	status   Status
	logFile  *os.File
	restarts int

	uvProject string
	args      []string
	// restartRequested is set by Restart: the next exit is a restart,
	// whatever its exit code, never a stop or a crash.
	restartRequested bool
}

func NewProcessBackend(logDir string) (*ProcessBackend, error) {
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, fmt.Errorf("creating log dir: %w", err)
	}
	return &ProcessBackend{
		logDir:     logDir,
		runners:    map[string]*runnerProc{},
		command:    runnerCommand,
		minBackoff: time.Second,
	}, nil
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
	rp := &runnerProc{cmd: cmd, endpoint: endpoint, status: StatusStarting, logFile: logFile,
		uvProject: uvProject, args: args}
	b.runners[name] = rp

	go b.supervise(name, rp)

	return endpoint, nil
}

// supervise waits on the process and restarts it: at once after a
// Restart, whatever the exit code, and with backoff after a crash, so a
// runner that dies immediately every time is not hot-looped. A clean exit
// nobody asked for is a stop.
func (b *ProcessBackend) supervise(name string, rp *runnerProc) {
	backoff := b.minBackoff
	for {
		err := rp.cmd.Wait()
		b.mu.Lock()
		stillTracked := b.runners[name] == rp
		b.mu.Unlock()
		if !stillTracked {
			return // explicitly stopped/replaced, not a crash
		}
		b.mu.Lock()
		restart := rp.restartRequested
		rp.restartRequested = false
		if !restart && err == nil {
			rp.status = StatusStopped
			b.mu.Unlock()
			return
		}
		rp.restarts++
		if !restart {
			rp.status = StatusCrashed
		}
		b.mu.Unlock()

		// A requested restart goes straight back up; only a crash waits.
		if !restart {
			time.Sleep(backoff)
			if backoff < 30*time.Second {
				backoff *= 2
			}
		}

		b.mu.Lock()
		cmd := b.command(rp.uvProject, rp.args)
		cmd.Stdout = rp.logFile
		cmd.Stderr = rp.logFile
		if err := cmd.Start(); err != nil {
			b.mu.Unlock()
			return
		}
		rp.cmd = cmd
		rp.status = StatusStarting
		b.mu.Unlock()
	}
}

// Stop and Restart send SIGTERM, not SIGKILL -- plan.md's resolution of
// the allow_shutdown question: the daemon ends a runner's life with a
// clean signal it can catch and shut down on, rather than routing
// through the runner's own HTTP shutdown API at all. Keeps
// `allow_shutdown` off for daemon-managed runners with no conflict
// between two controllers of the same lifecycle.

func (b *ProcessBackend) Stop(name string) error {
	b.mu.Lock()
	rp, ok := b.runners[name]
	if ok {
		delete(b.runners, name) // supervise() sees this and stops restarting
	}
	b.mu.Unlock()
	if !ok {
		return fmt.Errorf("no runner named %q", name)
	}
	if err := rp.cmd.Process.Signal(syscall.SIGTERM); err != nil {
		return err
	}
	rp.logFile.Close()
	return nil
}

func (b *ProcessBackend) Restart(name string) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok {
		return fmt.Errorf("no runner named %q", name)
	}
	// The intent is recorded, not inferred from the exit code: a runner
	// that exits 0 on SIGTERM is restarted all the same.
	rp.restartRequested = true
	return rp.cmd.Process.Signal(syscall.SIGTERM) // supervise() restarts it
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

// MarkRunning is called once the registry confirms /api/auth answers --
// plan.md's "knowing it actually started" health check, not this
// package's concern to poll, only to record.
func (b *ProcessBackend) MarkRunning(name string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if rp, ok := b.runners[name]; ok {
		rp.status = StatusRunning
	}
}
