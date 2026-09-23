package backend

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"sync"
	"syscall"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
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
	// stopTimeout is how long Stop and Restart wait after SIGTERM before
	// SIGKILL.
	stopTimeout time.Duration
	// maxLogSize caps a runner's captured log (0: no cap), checked every
	// logCheckInterval.
	maxLogSize       int64
	logCheckInterval time.Duration
	// ready says whether a runner has passed the readiness handshake yet;
	// probed every probeInterval after each start until it does.
	ready         func(ep endpoint.Endpoint, rootPath, aud string, key [32]byte) bool
	probeInterval time.Duration

	// front: where front-dirs go and how the probe is signed (SetFront).
	front FrontOptions

	mu      sync.Mutex
	runners map[string]*runnerProc
}

// runnerProc is one registered runner. The fixed fields (ep, dir,
// tempDir, aud, rootPath, uvProject, args, env, policy, wake) are set
// once at Start; logFile and logClosed are guarded by logMu; everything
// else by ProcessBackend.mu.
type runnerProc struct {
	ep        endpoint.Endpoint
	dir       string // the front-dir
	tempDir   bool   // dir was made by os.MkdirTemp: removed at Stop
	aud       string
	rootPath  string
	uvProject string
	args      []string
	env       []string
	policy    string

	key [32]byte // the current incarnation's key
	// frontRetried: an exit 4 has had its one respawn; cleared once an
	// incarnation reaches running, and by Restart.
	frontRetried bool

	cmd      *exec.Cmd // the current incarnation
	alive    bool      // cmd is running (not yet reaped by supervise)
	status   Status
	restarts int
	// reachedRunning: the current incarnation answered its probe, so the
	// crash backoff starts again from the bottom.
	reachedRunning bool
	// restartRequested is set by Restart: the next exit is a restart,
	// whatever its exit code, never a stop or a crash.
	restartRequested bool
	// stopping is set by Stop: the next exit is the end, no restart.
	stopping bool
	// supervising: a supervise() goroutine owns the runner; done is
	// closed when it returns.
	supervising bool
	done        chan struct{}

	wake chan struct{} // Stop/Restart cut a backoff wait short

	logMu     sync.Mutex
	logFile   *os.File
	logClosed bool
}

// NewProcessBackend keeps runner logs in logDir. Logs can hold secrets (a
// `?token=` in an access-log line), so the directory is made 0700 and each
// file 0600 -- also when they already exist with wider modes.
func NewProcessBackend(logDir string, maxLogSize int64) (*ProcessBackend, error) {
	if err := os.MkdirAll(logDir, 0o700); err != nil {
		return nil, fmt.Errorf("creating log dir: %w", err)
	}
	if err := os.Chmod(logDir, 0o700); err != nil {
		return nil, fmt.Errorf("restricting log dir: %w", err)
	}
	b := &ProcessBackend{
		logDir:           logDir,
		runners:          map[string]*runnerProc{},
		command:          runnerCommand,
		minBackoff:       time.Second,
		stopTimeout:      10 * time.Second,
		maxLogSize:       maxLogSize,
		logCheckInterval: 2 * time.Second,
		probeInterval:    500 * time.Millisecond,
	}
	b.ready = b.handshake
	return b, nil
}

// FrontOptions configure the front's side of every runner's channel.
type FrontOptions struct {
	// Root is the parent of the runners' front-dirs (frontdir.Root: under
	// systemd /run/flyball, else $XDG_RUNTIME_DIR/flyball/<front-id>).
	// "" gives each runner an os.MkdirTemp dir, removed at Stop.
	Root string
	// Sign signs the readiness probe (principal.Mint under the key and
	// aud given). nil: the signed half of the handshake cannot be made,
	// so no runner ever shows running.
	Sign endpoint.Signer
}

// SetFront sets the front options for runners started afterwards (Root)
// and for every probe from now on (Sign). Call it before Start.
func (b *ProcessBackend) SetFront(o FrontOptions) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.front = o
}

// handshake is the default readiness check: endpoint.Handshake with the
// front's signer.
func (b *ProcessBackend) handshake(ep endpoint.Endpoint, rootPath, aud string, key [32]byte) bool {
	b.mu.Lock()
	sign := b.front.Sign
	b.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 2*endpoint.ProbeTimeout)
	defer cancel()
	_, err := endpoint.Handshake(ctx, ep, rootPath, aud, key, sign)
	return err == nil
}

// Channel hands a front what it needs to reach runner name.
func (b *ProcessBackend) Channel(name string) (Channel, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok {
		return Channel{}, fmt.Errorf("no runner named %q", name)
	}
	return Channel{Endpoint: rp.ep, Dir: rp.dir, Aud: rp.aud, Key: rp.key}, nil
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

func (b *ProcessBackend) Start(name string, spec Spec) (string, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, taken := b.runners[name]; taken {
		return "", fmt.Errorf("a runner named %q is already running", name)
	}
	policy := spec.Restart
	switch policy {
	case "":
		policy = RestartOnFailure
	case RestartOnFailure, RestartAlways, RestartNever:
	default:
		return "", fmt.Errorf("runner %s: restart %q: use always, on-failure or never", name, policy)
	}

	network := spec.Network
	if network == "" {
		network = "unix"
		if runtime.GOOS == "windows" {
			network = "tcp"
		}
	}
	switch network {
	case "unix":
	case "tcp":
		if spec.Port <= 0 || spec.Port > 65535 {
			return "", fmt.Errorf("runner %s: network tcp needs a port, not %d", name, spec.Port)
		}
	default:
		return "", fmt.Errorf("runner %s: network %q: use unix or tcp", name, network)
	}
	aud := spec.Aud
	if aud == "" {
		aud = name
	}

	dir, tempDir := spec.FrontDir, false
	if dir != "" {
		if err := frontdir.Prepare(dir); err != nil {
			return "", fmt.Errorf("runner %s: %w", name, err)
		}
	} else {
		d, err := frontdir.Dir(b.front.Root, name)
		if err != nil {
			return "", fmt.Errorf("runner %s: %w", name, err)
		}
		dir, tempDir = d, b.front.Root == "" || filepath.Dir(d) != filepath.Clean(b.front.Root)
	}
	ep := endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, frontdir.Sock)}
	if network == "tcp" {
		host := spec.Host
		if host == "" {
			host = "127.0.0.1"
		}
		ep = endpoint.Endpoint{Network: "tcp", Address: net.JoinHostPort(host, strconv.Itoa(spec.Port))}
	}
	if err := ep.Validate(); err != nil {
		if tempDir {
			os.RemoveAll(dir)
		}
		return "", fmt.Errorf("runner %s: %w", name, err)
	}

	logPath := filepath.Join(b.logDir, name+".log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		if tempDir {
			os.RemoveAll(dir)
		}
		return "", fmt.Errorf("opening log file for %s: %w", name, err)
	}
	if err := logFile.Chmod(0o600); err != nil {
		logFile.Close()
		if tempDir {
			os.RemoveAll(dir)
		}
		return "", fmt.Errorf("restricting log file for %s: %w", name, err)
	}

	// No --host/--port: the runner binds what <front-dir>/endpoint says.
	args := []string{spec.ServerConfig, "--front-dir", dir}
	if spec.RootPath != "" {
		// The runner needs its own root_path to recognise the full,
		// un-stripped prefixed path the daemon's proxy forwards
		// (api.go's handleLandingOrProxy).
		args = append(args, "--root-path", spec.RootPath)
	}
	rp := &runnerProc{
		ep:        ep,
		dir:       dir,
		tempDir:   tempDir,
		aud:       aud,
		rootPath:  spec.RootPath,
		uvProject: spec.UvProject,
		args:      args,
		env:       append([]string(nil), spec.Env...),
		policy:    policy,
		wake:      make(chan struct{}, 1),
		logFile:   logFile,
	}
	cmd, err := b.spawn(rp)
	switch {
	case errors.Is(err, frontdir.ErrLive):
		// Another runner is alive in this front-dir: registered busy, not
		// spawned, its key untouched. (Adopting it is Phase 2.)
		b.runners[name] = rp
	case err != nil:
		logFile.Close()
		if tempDir {
			os.RemoveAll(dir)
		}
		return "", fmt.Errorf("starting runner %s: %w", name, err)
	default:
		b.runners[name] = rp
		b.superviseFrom(rp, cmd)
	}
	if b.maxLogSize > 0 {
		go b.capLog(rp, logPath)
	}
	return rp.ep.String(), nil
}

// spawn starts a new incarnation of rp and its readiness probe. b.mu held.
//
// Deliberately no death-of-parent signal: a daemon crash must not kill its
// runners, so default Unix reparenting is what we want.
//
// Every incarnation starts from the whole command: the front-dir is
// re-checked and rewritten with a fresh key (never while its runner.lock
// is held: then rp is busy and frontdir.ErrLive is returned), and the
// command is rebuilt with the same argv and env.
func (b *ProcessBackend) spawn(rp *runnerProc) (*exec.Cmd, error) {
	key, err := frontdir.Write(rp.dir, rp.aud, rp.ep)
	if errors.Is(err, frontdir.ErrLive) {
		rp.status = StatusBusy
		fmt.Fprintf(rp.logFile, "flyballd: %s: a runner already holds %s; not spawning another\n", rp.dir, frontdir.Lock)
		return nil, err
	}
	if err != nil {
		return nil, err
	}
	rp.key = key
	cmd := b.command(rp.uvProject, rp.args)
	if len(rp.env) > 0 {
		if cmd.Env == nil {
			cmd.Env = os.Environ()
		}
		cmd.Env = append(cmd.Env, rp.env...)
	}
	cmd.Stdout = rp.logFile
	cmd.Stderr = rp.logFile
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	rp.cmd = cmd
	rp.alive = true
	rp.status = StatusStarting
	rp.reachedRunning = false
	go b.probe(rp, cmd, key)
	return cmd, nil
}

// flyball-runner's exit codes that no restart policy applies to.
const (
	// exitBadConfig: a rig file that does not validate or a rig that
	// cannot be built (and argparse's, and uv's, for a bad command line or
	// project -- including a runner too old to know --front-dir).
	// Restarting cannot fix it: failed until someone Restarts it.
	exitBadConfig = 2
	// exitRigBusy: another runner holds the rig's <store>.lock.
	// Restarting would hit the same lock: busy until someone Restarts it.
	exitRigBusy = 3
	// exitFrontDir: the runner found its front-dir unsafe or incomplete.
	// The front rewrites it and respawns once, whatever the policy; a
	// second exit 4 in a row leaves it failed.
	exitFrontDir = 4
)

// superviseFrom hands rp to a new supervise() goroutine. b.mu held.
func (b *ProcessBackend) superviseFrom(rp *runnerProc, cmd *exec.Cmd) {
	rp.supervising = true
	rp.done = make(chan struct{})
	go b.supervise(rp, cmd, rp.done)
}

// supervise waits on the process and decides what its exit means. The
// intent is explicit, never inferred from the exit code alone:
//
//   - Stop: the end, whatever the exit code.
//   - Restart: up again at once, whatever the exit code.
//   - neither: the manifest's restart policy -- on-failure (the default)
//     restarts after a crash, always after any exit, never not at all --
//     with a backoff, so a runner that dies immediately every time is not
//     hot-looped. Exit 2 is never restarted: see exitBadConfig.
func (b *ProcessBackend) supervise(rp *runnerProc, cmd *exec.Cmd, done chan struct{}) {
	defer close(done)
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
		code := cmd.ProcessState.ExitCode()
		if !restart && code == exitFrontDir && !rp.frontRetried {
			rp.frontRetried = true
			fmt.Fprintf(rp.logFile, "flyballd: exit 4: the runner refused its front-dir %s; rewriting it and respawning once\n", rp.dir)
			restart = true
		}
		if !restart {
			crashed := err != nil
			again := rp.policy == RestartAlways || (crashed && rp.policy == RestartOnFailure)
			final := StatusStopped
			if crashed {
				final = StatusFailed
			}
			switch code {
			case exitBadConfig:
				again = false
				fmt.Fprintf(rp.logFile, "flyballd: exit 2: a bad rig file, or a flyball-runner too old for --front-dir; not restarting\n")
			case exitRigBusy:
				again, final = false, StatusBusy
			case exitFrontDir:
				again = false
			}
			if !again {
				rp.status = final
				rp.supervising = false
				b.mu.Unlock()
				return
			}
			rp.status = StatusRestarting
			if rp.reachedRunning {
				backoff = b.minBackoff
			}
			b.mu.Unlock()

			// Stop or Restart cut the wait short.
			select {
			case <-time.After(backoff):
			case <-rp.wake:
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}

			b.mu.Lock()
			if rp.stopping {
				rp.status = StatusStopped
				b.mu.Unlock()
				return
			}
			rp.restartRequested = false
		}
		rp.restarts++
		cmd, err = b.spawn(rp)
		if errors.Is(err, frontdir.ErrLive) {
			rp.supervising = false // busy, set by spawn
			b.mu.Unlock()
			return
		}
		if err != nil {
			fmt.Fprintf(rp.logFile, "flyballd: restarting: %v\n", err)
			rp.status = StatusFailed
			rp.supervising = false
			b.mu.Unlock()
			return
		}
		b.mu.Unlock()
	}
}

// probe marks one incarnation running once it answers, and gives up when
// that incarnation is no longer the live one.
func (b *ProcessBackend) probe(rp *runnerProc, cmd *exec.Cmd, key [32]byte) {
	for {
		b.mu.Lock()
		current := rp.cmd == cmd && rp.alive
		b.mu.Unlock()
		if !current {
			return
		}
		if b.ready(rp.ep, rp.rootPath, rp.aud, key) {
			b.mu.Lock()
			if rp.cmd == cmd && rp.alive && rp.status == StatusStarting {
				rp.status = StatusRunning
				rp.reachedRunning = true
				rp.frontRetried = false
			}
			b.mu.Unlock()
			return
		}
		time.Sleep(b.probeInterval)
	}
}

// capLog keeps a runner's captured log under maxLogSize (log_max_size):
// past it, the file is copied to NAME.log.1 (replacing the last one) and
// truncated. The runner keeps its own descriptor (O_APPEND, so it writes
// on at the new end) -- no pipe through the daemon, so a daemon crash
// does not break the runner's stdout. The cost: the file can overshoot by
// what the runner writes in one check interval, and a line written
// between the copy and the truncate is lost.
func (b *ProcessBackend) capLog(rp *runnerProc, path string) {
	t := time.NewTicker(b.logCheckInterval)
	defer t.Stop()
	for range t.C {
		rp.logMu.Lock()
		if rp.logClosed {
			rp.logMu.Unlock()
			return
		}
		if fi, err := rp.logFile.Stat(); err == nil && fi.Size() > b.maxLogSize {
			if err := copyFile(path, path+".1"); err == nil {
				rp.logFile.Truncate(0)
			}
		}
		rp.logMu.Unlock()
	}
}

func copyFile(from, to string) error {
	src, err := os.Open(from)
	if err != nil {
		return err
	}
	defer src.Close()
	dst, err := os.OpenFile(to, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if err := dst.Chmod(0o600); err != nil {
		dst.Close()
		return err
	}
	if _, err := io.Copy(dst, src); err != nil {
		dst.Close()
		return err
	}
	return dst.Close()
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
	cmd := rp.cmd
	delete(b.runners, name)
	rp.stopping = true
	if rp.alive {
		rp.cmd.Process.Signal(syscall.SIGTERM)
	}
	b.nudge(rp)
	done := rp.done
	b.mu.Unlock()

	if done != nil { // nil: never spawned (busy from the start)
		select {
		case <-done:
		case <-time.After(b.stopTimeout):
			b.killIfStill(rp, cmd)
			<-done
		}
	}
	rp.logMu.Lock()
	rp.logClosed = true
	rp.logFile.Close()
	rp.logMu.Unlock()
	if rp.tempDir {
		os.RemoveAll(rp.dir)
	}
	return nil
}

// Restart ends the runner's current process and starts a new one at once:
// SIGTERM, then SIGKILL if that process is still there after stopTimeout,
// as Stop does -- so a runner stuck in its shutdown (a read hung in a
// driver) is not waited on for ever. It returns once SIGTERM is sent.
// The intent is recorded, not inferred from the exit code: a runner that
// exits 0 on SIGTERM, or is killed, is restarted all the same. A runner in
// crash backoff restarts now; one that has stopped or failed starts again.
func (b *ProcessBackend) Restart(name string) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok {
		return fmt.Errorf("no runner named %q", name)
	}
	rp.frontRetried = false
	if !rp.supervising {
		cmd, err := b.spawn(rp)
		if errors.Is(err, frontdir.ErrLive) {
			return fmt.Errorf("restarting runner %s: %w", name, err)
		}
		if err != nil {
			rp.status = StatusFailed
			return fmt.Errorf("restarting runner %s: %w", name, err)
		}
		rp.restarts++
		b.superviseFrom(rp, cmd)
		return nil
	}
	rp.restartRequested = true
	if rp.alive {
		cmd := rp.cmd
		if err := cmd.Process.Signal(syscall.SIGTERM); err != nil { // supervise() restarts it
			return err
		}
		time.AfterFunc(b.stopTimeout, func() { b.killIfStill(rp, cmd) })
		return nil
	}
	b.nudge(rp)
	return nil
}

// killIfStill sends SIGKILL to cmd if it is still rp's live process -- not
// reaped, nor replaced by a later incarnation. Takes b.mu.
func (b *ProcessBackend) killIfStill(rp *runnerProc, cmd *exec.Cmd) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if rp.alive && rp.cmd == cmd {
		cmd.Process.Kill()
	}
}

// nudge wakes supervise() from a backoff wait, if it is in one.
func (b *ProcessBackend) nudge(rp *runnerProc) {
	select {
	case rp.wake <- struct{}{}:
	default:
	}
}

func (b *ProcessBackend) Logs(name string) (io.ReadCloser, error) {
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
