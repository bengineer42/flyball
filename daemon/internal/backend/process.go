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

// ProcessBackend is option 1 from plan.md's Sequencing section: raw
// subprocesses, no isolation (the honest caveat already flagged there --
// less sandboxing than dev-serve's Docker containers). Spawns the real,
// literal `flyball-daemon` CLI -- plan.md's Terminology section on the
// naming collision this creates in prose, kept as-is here since it's the
// actual command.
type ProcessBackend struct {
	logDir string

	mu      sync.Mutex
	runners map[string]*runnerProc
}

type runnerProc struct {
	cmd      *exec.Cmd
	endpoint string
	status   Status
	logFile  *os.File
	restarts int
}

func NewProcessBackend(logDir string) (*ProcessBackend, error) {
	if err := os.MkdirAll(logDir, 0o755); err != nil {
		return nil, fmt.Errorf("creating log dir: %w", err)
	}
	return &ProcessBackend{logDir: logDir, runners: map[string]*runnerProc{}}, nil
}

func (b *ProcessBackend) Start(name, serverConfig, host string, port int) (string, error) {
	b.mu.Lock()
	defer b.mu.Unlock()

	logPath := filepath.Join(b.logDir, name+".log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return "", fmt.Errorf("opening log file for %s: %w", name, err)
	}

	cmd := exec.Command(
		"flyball-daemon", serverConfig,
		"--host", host,
		"--port", fmt.Sprintf("%d", port),
	)
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
	rp := &runnerProc{cmd: cmd, endpoint: endpoint, status: StatusStarting, logFile: logFile}
	b.runners[name] = rp

	go b.supervise(name, rp)

	return endpoint, nil
}

// supervise waits on the process and restarts it on crash, with backoff
// -- plan.md's "don't hot-loop a runner that dies immediately every
// time." Restart policy (always/on-failure/never) is layer 2's business;
// this loop just detects crash-vs-clean-stop, same distinction Docker
// draws.
func (b *ProcessBackend) supervise(name string, rp *runnerProc) {
	backoff := time.Second
	for {
		err := rp.cmd.Wait()
		b.mu.Lock()
		stillTracked := b.runners[name] == rp
		b.mu.Unlock()
		if !stillTracked {
			return // explicitly stopped/replaced, not a crash
		}
		if err == nil {
			b.mu.Lock()
			rp.status = StatusStopped
			b.mu.Unlock()
			return
		}
		b.mu.Lock()
		rp.status = StatusCrashed
		rp.restarts++
		b.mu.Unlock()

		time.Sleep(backoff)
		if backoff < 30*time.Second {
			backoff *= 2
		}

		b.mu.Lock()
		args := append([]string{}, rp.cmd.Args[1:]...)
		cmd := exec.Command(rp.cmd.Path, args...)
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
	rp, ok := b.runners[name]
	b.mu.Unlock()
	if !ok {
		return fmt.Errorf("no runner named %q", name)
	}
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
