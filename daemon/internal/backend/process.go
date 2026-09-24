package backend

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
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
	// adoptPoll is how often an adopted runner's pid is checked for life;
	// adoptWindow how long a held front-dir whose runner is not answering
	// yet (it may be starting) is waited for before it is left busy.
	adoptPoll   time.Duration
	adoptWindow time.Duration

	// front: where front-dirs go and how the probe is signed (SetFront).
	front FrontOptions

	mu      sync.Mutex
	runners map[string]*runnerProc
	// detached: Detach was called; nothing is respawned or probed again.
	detached bool
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

	cmd   *exec.Cmd // the current incarnation, when flyballd spawned it
	alive bool      // the current incarnation is running (not yet reaped, or seen gone)
	// An adopted incarnation (D-037): not flyballd's child, so cmd is nil
	// and it is watched by pid -- the one runner.lock names, with its
	// start time so a later process given the same pid is not mistaken
	// for it.
	adopted  bool
	pid      int
	pidStart string
	reason   string // why it is busy or failed, when known
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
		adoptPoll:        500 * time.Millisecond,
		adoptWindow:      60 * time.Second,
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
		if endpoint.GOOS == "windows" {
			network = "tcp"
		}
	}
	var warnings []string
	if network == "tcp" && !endpoint.TCPAllowed() {
		// D-044, and D-028: a misconfiguration removes exposure, never
		// operation. The unix socket is strictly less exposed.
		warnings = append(warnings, fmt.Sprintf("runner %s: network: tcp is refused except on Windows until the runner proves it holds the key (D-044);"+
			" it listens on the unix socket in its front-dir instead, and its port is unused", name))
		network = "unix"
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

	if network == "tcp" {
		warnings = append(warnings, fmt.Sprintf("runner %s: network: tcp, on %s: any local user can connect to that port"+
			" (the runner still demands the front's signed principal), and a process that binds it first is taken for the runner;"+
			" a unix socket in a 0700 front-dir has neither exposure", name, ep.Address))
	}
	if tempDir && b.front.Root != "" {
		warnings = append(warnings, fmt.Sprintf("runner %s: %s/%s/%s would be over %d bytes, so its front-dir is a temp dir (%s):"+
			" a restarted flyballd cannot find it, and this runner will not be adopted -- the next flyballd's runner exits 3 and the rig is busy"+
			" while this one runs on; use a shorter runtime dir", name, b.front.Root, name, frontdir.Sock, endpoint.MaxSocketPath, dir))
	}
	for _, w := range warnings {
		log.Printf("WARNING: %s", w)
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

	for _, w := range warnings {
		fmt.Fprintf(logFile, "flyballd: WARNING: %s\n", w)
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
		// A runner is alive in this front-dir: adopted if it proves to be
		// ours (D-037), else busy -- never spawned over, its key untouched.
		b.runners[name] = rp
		rp.status = StatusStarting
		b.superviseFrom(rp, nil)
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
// runners, so default Unix reparenting is what we want. Each runner has
// a process group of its own (ownGroup), so a signal to flyballd's group
// -- Ctrl-C in a terminal -- does not reach it either (D-037).
//
// Every incarnation starts from the whole command: the front-dir is
// re-checked and rewritten with a fresh key (never while its runner.lock
// is held: then rp is busy and frontdir.ErrLive is returned), and the
// command is rebuilt with the same argv and env.
func (b *ProcessBackend) spawn(rp *runnerProc) (*exec.Cmd, error) {
	key, err := frontdir.Write(rp.dir, rp.aud, rp.ep)
	if errors.Is(err, frontdir.ErrLive) {
		rp.status = StatusBusy
		rp.reason = fmt.Sprintf("a runner already holds %s in %s", frontdir.Lock, rp.dir)
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
	ownGroup(cmd)
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	rp.cmd = cmd
	rp.alive = true
	rp.adopted, rp.pid, rp.pidStart, rp.reason = false, 0, "", ""
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
	// Exit 5, the runner could not serve (its socket not bound, its
	// server not started), is not among them: a crash, restarted by the
	// policy. It was uvicorn's own 3 before, taken for busy for good.
)

// superviseFrom hands rp to a new supervise() goroutine. b.mu held. cmd
// nil: a runner already holds rp's front-dir, to be adopted or left busy.
func (b *ProcessBackend) superviseFrom(rp *runnerProc, cmd *exec.Cmd) {
	rp.supervising = true
	rp.done = make(chan struct{})
	go b.supervise(rp, cmd, rp.done)
}

// supervise owns rp until it ends: it watches each incarnation flyballd
// spawned (watch), and each it found already running (adopt), for as
// long as one follows another.
func (b *ProcessBackend) supervise(rp *runnerProc, cmd *exec.Cmd, done chan struct{}) {
	defer close(done)
	for {
		if cmd != nil && !b.watch(rp, cmd) {
			return
		}
		if cmd = b.adopt(rp); cmd == nil {
			return
		}
	}
}

// watch waits on a spawned process and decides what its exit means. The
// intent is explicit, never inferred from the exit code alone:
//
//   - Stop: the end, whatever the exit code.
//   - Restart: up again at once, whatever the exit code.
//   - neither: the manifest's restart policy -- on-failure (the default)
//     restarts after a crash, always after any exit, never not at all --
//     with a backoff, so a runner that dies immediately every time is not
//     hot-looped. Exit 2 is never restarted: see exitBadConfig.
//
// It returns true when a respawn found another runner holding the
// front-dir (for adopt), false when rp's supervision is over.
func (b *ProcessBackend) watch(rp *runnerProc, cmd *exec.Cmd) (live bool) {
	backoff := b.minBackoff
	for {
		err := cmd.Wait()
		b.mu.Lock()
		rp.alive = false
		if b.detached {
			rp.supervising = false
			b.mu.Unlock()
			return false
		}
		if rp.stopping {
			rp.status = StatusStopped
			b.mu.Unlock()
			return false
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
				rp.reason = "exit 2: a bad rig file, or a flyball-runner too old for --front-dir"
				fmt.Fprintf(rp.logFile, "flyballd: exit 2: a bad rig file, or a flyball-runner too old for --front-dir; not restarting\n")
			case exitRigBusy:
				if held, _ := frontdir.LockHeld(rp.dir); held {
					// A runner took this front-dir between its Write and
					// this spawn's start (one a flyballd before this one
					// started): not a busy rig but a runner to adopt.
					fmt.Fprintf(rp.logFile, "flyballd: exit 3: a runner holds %s in %s; adopting it\n", frontdir.Lock, rp.dir)
					rp.status = StatusStarting
					b.mu.Unlock()
					return true
				}
				again, final = false, StatusBusy
				rp.reason = "exit 3: another runner holds the rig's store lock"
			case exitFrontDir:
				again = false
				rp.reason = "exit 4 twice: the runner refused its front-dir " + rp.dir
			}
			if !again {
				rp.status = final
				rp.supervising = false
				b.mu.Unlock()
				return false
			}
			rp.status = StatusRestarting
			if rp.reachedRunning {
				backoff = b.minBackoff
			}
			if !b.backoff(rp, backoff) {
				return false
			}
			if backoff < 30*time.Second {
				backoff *= 2
			}
		}
		rp.restarts++
		cmd, err = b.spawn(rp)
		if errors.Is(err, frontdir.ErrLive) {
			rp.status = StatusStarting
			b.mu.Unlock()
			return true
		}
		if err != nil {
			fmt.Fprintf(rp.logFile, "flyballd: restarting: %v\n", err)
			rp.status = StatusFailed
			rp.reason = err.Error()
			rp.supervising = false
			b.mu.Unlock()
			return false
		}
		b.mu.Unlock()
	}
}

// backoff waits d before a respawn, cut short by Stop or Restart. b.mu
// held on entry; on true it is held again, on false (stopped or detached
// meanwhile) it has been released and rp's supervision is over.
func (b *ProcessBackend) backoff(rp *runnerProc, d time.Duration) bool {
	b.mu.Unlock()
	select {
	case <-time.After(d):
	case <-rp.wake:
	}
	b.mu.Lock()
	if rp.stopping || b.detached {
		rp.status = StatusStopped
		rp.supervising = false
		b.mu.Unlock()
		return false
	}
	rp.restartRequested = false
	return true
}

// adopt takes over a runner found alive in rp's front-dir (D-037): once
// it answers the signed handshake with the front-dir's key and rp's aud,
// it is running, with no restart and its key untouched, and is watched
// by the pid its runner.lock names; when it goes, rp's restart policy
// applies as to a crash (its exit status cannot be known: flyballd is not
// its parent). A runner that is not ours -- the handshake fails, the
// front-dir's aud is another's, runner.lock names no pid -- leaves rp
// busy, with the reason; one that does not answer is waited for
// (adoptWindow: it may be starting) and then left busy. A front-dir that
// fails frontdir.Check is never adopted. It returns the incarnation it
// spawned once the front-dir came free, or nil when rp's supervision is
// over.
func (b *ProcessBackend) adopt(rp *runnerProc) *exec.Cmd {
	for {
		cmd, adopted := b.takeOver(rp)
		if !adopted {
			return cmd
		}
		cmd, live := b.watchAdopted(rp)
		if !live {
			return cmd
		}
	}
}

// takeOver is adopt's first half: adopted true once rp runs the runner
// found in its front-dir; otherwise the incarnation spawned into a
// front-dir found free, or nil (busy, stopped, detached, failed).
func (b *ProcessBackend) takeOver(rp *runnerProc) (cmd *exec.Cmd, adopted bool) {
	deadline := time.Now().Add(b.adoptWindow)
	for {
		b.mu.Lock()
		if rp.stopping || b.detached {
			rp.status = StatusStopped
			rp.supervising = false
			b.mu.Unlock()
			return nil, false
		}
		sign := b.front.Sign
		b.mu.Unlock()

		h, err := inspect(rp.dir, rp.aud)
		if err == nil && !h.held {
			// The runner went meanwhile: the front-dir is free, spawn as
			// for a dead one.
			b.mu.Lock()
			cmd, err := b.spawn(rp)
			switch {
			case errors.Is(err, frontdir.ErrLive):
				rp.status = StatusStarting
				b.mu.Unlock()
				continue
			case err != nil:
				rp.status, rp.reason = StatusFailed, err.Error()
				rp.supervising = false
				b.mu.Unlock()
				return nil, false
			}
			b.mu.Unlock()
			return cmd, false
		}
		// A runner that has taken the lock but not yet named itself in it
		// is starting, as is one not listening yet or not answering yet
		// (a probe that times out, or a connection closed unanswered: a
		// Pi takes ~20 s to start). Each is retried within adoptWindow;
		// only an answer that proves the runner is not ours makes it busy.
		if errors.Is(err, errNoPid) && time.Now().Before(deadline) {
			b.pause(rp, b.probeInterval)
			continue
		}
		var info endpoint.FrontInfo
		if err == nil {
			ctx, cancel := context.WithTimeout(context.Background(), 2*endpoint.ProbeTimeout)
			info, err = endpoint.Handshake(ctx, h.ep, rp.rootPath, rp.aud, h.key, sign)
			cancel()
			if errors.Is(err, endpoint.ErrNotListening) || noAnswer(err) {
				if time.Now().Before(deadline) {
					b.pause(rp, b.probeInterval)
					continue
				}
				err = fmt.Errorf("its runner (pid %d) is not answering at %s after %v: %w", h.pid, h.ep, b.adoptWindow, err)
			}
		}

		b.mu.Lock()
		if rp.stopping || b.detached {
			rp.status = StatusStopped
			rp.supervising = false
			b.mu.Unlock()
			return nil, false
		}
		if err != nil {
			rp.status = StatusBusy
			rp.reason = fmt.Sprintf("%s is held by a runner flyballd cannot adopt: %v", filepath.Join(rp.dir, frontdir.Lock), err)
			rp.supervising = false
			fmt.Fprintf(rp.logFile, "flyballd: %s; not adopting it, not spawning another, its key left alone\n", rp.reason)
			b.mu.Unlock()
			return nil, false
		}
		if info.Pid != 0 && info.Pid != h.pid {
			fmt.Fprintf(rp.logFile, "flyballd: %s names pid %d, the runner answers as pid %d; watching %d\n", frontdir.Lock, h.pid, info.Pid, h.pid)
		}
		rp.key, rp.ep = h.key, h.ep
		rp.cmd, rp.alive = nil, true
		rp.adopted, rp.pid, rp.pidStart, rp.reason = true, h.pid, h.start, ""
		rp.status = StatusRunning
		rp.reachedRunning = true
		rp.frontRetried = false
		fmt.Fprintf(rp.logFile, "flyballd: adopted the runner already running in %s (pid %d), without a restart\n", rp.dir, h.pid)
		b.mu.Unlock()
		return nil, true
	}
}

// watchAdopted is adopt's second half: it polls the adopted pid until it
// is gone, then applies Stop, Restart or the policy. It returns the
// incarnation it spawned, or live true when another runner holds the
// front-dir by then (adopt again), or neither when supervision is over.
func (b *ProcessBackend) watchAdopted(rp *runnerProc) (cmd *exec.Cmd, live bool) {
	for {
		b.pause(rp, b.adoptPoll)
		b.mu.Lock()
		if b.detached {
			rp.supervising = false
			b.mu.Unlock()
			return nil, false
		}
		if processAlive(rp.pid, rp.pidStart) {
			b.mu.Unlock()
			continue
		}
		// The pid is gone or a zombie; a zombie's other threads may still
		// be letting go of runner.lock, so wait for that (briefly) too.
		b.mu.Unlock()
		for end := time.Now().Add(2 * time.Second); time.Now().Before(end); time.Sleep(10 * time.Millisecond) {
			if held, err := frontdir.LockHeld(rp.dir); err != nil || !held {
				break
			}
		}
		b.mu.Lock()
		if b.detached {
			rp.supervising = false
			b.mu.Unlock()
			return nil, false
		}
		rp.alive = false
		fmt.Fprintf(rp.logFile, "flyballd: the adopted runner (pid %d) has gone\n", rp.pid)
		if rp.stopping {
			rp.status = StatusStopped
			b.mu.Unlock()
			return nil, false
		}
		restart := rp.restartRequested
		rp.restartRequested = false
		if !restart {
			if rp.policy == RestartNever {
				rp.status = StatusFailed
				rp.reason = "the adopted runner exited (its exit status cannot be known; restart: never)"
				rp.supervising = false
				b.mu.Unlock()
				return nil, false
			}
			rp.status = StatusRestarting
			if !b.backoff(rp, b.minBackoff) {
				return nil, false
			}
		}
		rp.restarts++
		cmd, err := b.spawn(rp)
		if errors.Is(err, frontdir.ErrLive) {
			rp.status = StatusStarting
			b.mu.Unlock()
			return nil, true
		}
		if err != nil {
			fmt.Fprintf(rp.logFile, "flyballd: restarting: %v\n", err)
			rp.status, rp.reason = StatusFailed, err.Error()
			rp.supervising = false
			b.mu.Unlock()
			return nil, false
		}
		b.mu.Unlock()
		return cmd, false
	}
}

// pause waits d, cut short by Stop or Restart (rp.wake).
func (b *ProcessBackend) pause(rp *runnerProc, d time.Duration) {
	select {
	case <-time.After(d):
	case <-rp.wake:
	}
}

// holder is what a front-dir says of the runner holding it.
type holder struct {
	held  bool
	key   [32]byte
	ep    endpoint.Endpoint
	pid   int
	start string
}

var lockPid = regexp.MustCompile(`^pid (\d+)`)

// errNoPid: runner.lock is held but names no pid -- its runner has taken
// it and not yet written its pid (a front's Write leaves it empty).
var errNoPid = errors.New(frontdir.Lock + " names no pid")

// noAnswer: a handshake error that is not an answer -- a probe that timed
// out, or a connection closed or reset before its answer came -- as
// against one that proves the runner is not this front's (a wrong status,
// protocol or aud). front/proxy.go's noAnswer reads it the same way.
func noAnswer(err error) bool {
	var ne net.Error
	return errors.As(err, &ne) && ne.Timeout() ||
		errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) ||
		errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) ||
		errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.EPIPE)
}

// inspect reads the front-dir dir of a runner for aud: whether its
// runner.lock is held, and if so the key, endpoint and pid a runner there
// was given. An error: the dir fails frontdir.Check, or it holds what no
// runner of aud's would have been given.
func inspect(dir, aud string) (holder, error) {
	var h holder
	if err := frontdir.Check(dir); err != nil {
		return h, err
	}
	held, err := frontdir.LockHeld(dir)
	if err != nil || !held {
		return h, err
	}
	h.held = true
	r, err := os.OpenRoot(dir)
	if err != nil {
		return h, err
	}
	defer r.Close()
	lock, err := r.ReadFile(frontdir.Lock)
	if err != nil {
		return h, err
	}
	if got, err := r.ReadFile(frontdir.Aud); err != nil {
		return h, err
	} else if a := strings.TrimSpace(string(got)); a != aud {
		return h, fmt.Errorf("its aud is %q, this runner's is %q", a, aud)
	}
	raw, err := r.ReadFile(frontdir.EndpointFile)
	if err != nil {
		return h, err
	}
	if h.ep, err = endpoint.Parse(strings.TrimSpace(string(raw))); err != nil {
		return h, err
	}
	if h.key, err = frontdir.ReadKey(dir); err != nil {
		return h, err
	}
	// Last: a dir written for this runner whose lock names no pid yet is
	// one whose runner is starting (errNoPid), not a foreign one.
	m := lockPid.FindSubmatch(lock)
	if m == nil {
		return h, fmt.Errorf("%w (%q)", errNoPid, strings.TrimSpace(string(lock)))
	}
	h.pid, _ = strconv.Atoi(string(m[1]))
	h.start = processStart(h.pid)
	return h, nil
}

// probe marks one incarnation running once it answers, and gives up when
// that incarnation is no longer the live one.
func (b *ProcessBackend) probe(rp *runnerProc, cmd *exec.Cmd, key [32]byte) {
	for {
		b.mu.Lock()
		current := rp.cmd == cmd && rp.alive && !b.detached
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
		b.mu.Lock()
		detached := b.detached
		b.mu.Unlock()
		if detached {
			return
		}
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
		b.signal(rp, syscall.SIGTERM)
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
		if err := b.signal(rp, syscall.SIGTERM); err != nil { // supervise() restarts it
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
		b.signal(rp, os.Kill)
	}
}

// signal sends sig to rp's live process: the one flyballd spawned, or the
// adopted pid, if it is still the process that was adopted. b.mu held.
// SIGKILL to a spawned runner goes to its whole process group: under
// `uv_project:` the process flyballd spawned is uv, which forwards SIGTERM
// to the runner but cannot forward SIGKILL. SIGTERM goes to that process
// alone, so the runner gets it once. An adopted pid is the runner itself.
func (b *ProcessBackend) signal(rp *runnerProc, sig os.Signal) error {
	if rp.cmd != nil {
		if sig == os.Kill {
			return killGroup(rp.cmd.Process)
		}
		return rp.cmd.Process.Signal(sig)
	}
	if !rp.adopted || !processAlive(rp.pid, rp.pidStart) {
		return nil
	}
	p, err := os.FindProcess(rp.pid)
	if err != nil {
		return err
	}
	return p.Signal(sig)
}

// Detach lets go of every runner without ending any (D-037): flyballd is
// exiting, and its runners carry on for the next flyballd to adopt. No
// runner is respawned, probed or signalled afterwards; a runner that exits
// meanwhile is the next flyballd's to respawn. The processes' log files
// are theirs (each holds its own descriptor), so nothing is closed.
func (b *ProcessBackend) Detach() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.detached = true
	for _, rp := range b.runners {
		b.nudge(rp)
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

// Detail is runner name's status, endpoint, live pid, whether it was
// adopted, and why it is busy or failed.
func (b *ProcessBackend) Detail(name string) (Detail, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	rp, ok := b.runners[name]
	if !ok {
		return Detail{}, fmt.Errorf("no runner named %q", name)
	}
	d := Detail{Status: rp.status, Endpoint: rp.ep.String(), Adopted: rp.adopted && rp.alive, Reason: rp.reason}
	switch {
	case !rp.alive:
	case rp.cmd != nil && rp.cmd.Process != nil:
		d.Pid = rp.cmd.Process.Pid
	default:
		d.Pid = rp.pid
	}
	return d, nil
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
