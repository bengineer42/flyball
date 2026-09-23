package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front"
	"flyballd/internal/frontwire"
)

// runDirect is `flyball run RIG-FILE [RIG-FILE...] [--listen ADDR] [--uv]
// [--insecure-open] [flyball-runner flags...]`: one rig, in the
// foreground, no flyballd. The front reads the rig files and --sets the
// runner merges (runLayers, D-046); the first file keys the front-dir. It starts the front (serve_ui.go) and runs
// flyball-runner behind it, fronted: the runner gets a front-dir
// (`--front-dir DIR`: a fresh key, its aud `run-<8 hex>`, its endpoint, a
// socket in DIR) and is reachable only through the front. A runner that
// crashes is started again with a fresh key; one that exits cleanly, with
// a bad rig file (2), a busy rig (3) or twice in a row an unusable
// front-dir (4) ends the run.
//
// A bare runner (its own TCP port, a token) is reached only by running
// flyball-runner directly.
//
// --uv runs `flyball-runner` via `uv run --project <dir>`, <dir> being the
// rig file's own directory: the bare exec only works when flyball-runner
// is already on $PATH, which it never is outside an app's own uv-managed
// venv.
func runDirect(args []string) error {
	// A write to a stdout or stderr whose reader has died (`flyball run
	// ... | tee out.txt` over SSH, the connection dropped) would kill this
	// process with SIGPIPE and orphan the runner (D-038). Ignored, it
	// fails with EPIPE, which the tee drops (runlog.go).
	signal.Ignore(syscall.SIGPIPE)
	// Every Ctrl-C (and SIGTERM) stays caught for the whole run: each one
	// escalates the runner's stop (run), and flyball ends only once the
	// runner has (D-045). SIGHUP is not among them (D-038).
	sigs := make(chan os.Signal, 3)
	signal.Notify(sigs, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(sigs)
	return run(args, sigs)
}

// runnerCommand is what a run execs when not going through uv; a variable
// so a test can stand a fake runner in for it.
var runnerCommand = "flyball-runner"

// uvCommand is `uv`, a variable for the same reason.
var uvCommand = "uv"

// runOut is where `flyball run` writes its own lines (the banner, the
// runner's exits); a variable for the tests.
var runOut io.Writer = os.Stderr

const runUsage = "usage: flyball run <rig-file> [<rig-file> ...] [--listen ADDR] [--uv] [--insecure-open] [--set KEY=VALUE ...] [flyball-runner flags...]"

// run is runDirect with the stop signals given (SIGINT or SIGTERM, each a
// press of Ctrl-C): the first goes on to the runner's group and ends the
// run, the second is SIGINT whatever arrived (uvicorn force-exits on a
// second SIGINT), the third SIGKILLs the group (D-045). run returns only
// once the runner has exited.
func run(args []string, sigs <-chan os.Signal) error {
	o := parseRunArgs(args)
	if len(o.rest) < 1 || strings.HasPrefix(o.rest[0], "-") {
		return errors.New(runUsage)
	}
	rig := o.rest[0] // the first rig file keys the front-dir, as it does the runner's
	files, sets, err := runLayers(o.rest)
	if err != nil {
		return err
	}
	doc, docErr := rigDocument(files, sets)
	runner, _ := doc["runner"].(map[string]any)
	cfg, useUV, bad, warnings := runFront(runner, o.listen)
	if docErr != nil {
		bad = docErr // runner.front cannot be read: fall back, never serve open silently
	}
	useUV = useUV || o.uv

	id, err := frontdir.FrontID(rig)
	if err != nil {
		return fmt.Errorf("front-dir for %s: %w", rig, err)
	}
	state, err := frontwire.RunDir(id)
	if err != nil {
		return err
	}
	rl, err := openRunLog(state)
	if err != nil {
		return err
	}
	defer rl.Close()
	out := rl.tee(runOut) // flyball run's own output: the terminal and run.log (D-038)

	logger := slog.New(slog.NewTextHandler(out, nil))
	audit := frontwire.OpenAudit(state, logger)
	plan, proxy := frontwire.Plan(cfg, bad, o.insecureOpen, frontwire.ProxyOptions{Logger: logger, Audit: audit})
	plan.Warnings = append(warnings, plan.Warnings...)
	if b := plan.Banner(); b != "" {
		for _, line := range strings.Split(b, "\n") {
			fmt.Fprintln(out, "flyball:", line)
		}
	}
	hup := make(chan os.Signal, 1)
	signal.Notify(hup, syscall.SIGHUP)
	defer signal.Stop(hup)
	go func() {
		for range hup {
			logger.Info("terminal hung up; the rig keeps running")
		}
	}()

	root := frontdir.Root(id)
	dir, err := frontdir.Dir(root, frontwire.RunRig) // frontwire.RunFrontDir(rig) when not a temp dir
	if err != nil {
		plan.Close()
		audit.Close()
		return err
	}
	if root == "" || filepath.Dir(dir) != filepath.Clean(root) {
		defer os.RemoveAll(dir) // a temp dir: ours alone
	}
	stop := "`flyball stop`"
	if plan.Refused != "" {
		// A bare `flyball stop` goes to the refused address and gets its 503.
		stop = "`flyball stop --front-dir " + dir + "`"
	}
	fmt.Fprintf(out, "flyball: closing this terminal does not stop the rig -- Ctrl-C or %s does; the log is %s; for a rig that survives reboots use flyballd\n", stop, rl.path)

	s := &supervisor{
		dir: dir, aud: "run-" + randomHex(4), rig: rig,
		ep:         endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, frontdir.Sock)},
		command:    runnerExec(useUV, rig, append(o.rest, "--front-dir", dir), rl.tee(os.Stdout), rl.tee(os.Stderr)),
		minBackoff: time.Second,
		wake:       make(chan struct{}, 1),
		out:        out,
	}
	if err := s.ep.Validate(); err != nil {
		plan.Close()
		audit.Close()
		return err
	}

	name, _ := doc["name"].(string)
	if name == "" {
		name = strings.TrimSuffix(filepath.Base(rig), filepath.Ext(rig))
	}
	fo := frontwire.Options(plan, proxy, audit, state, logger)
	fo.Route = front.SingleRig(front.Rig{Root: "", Name: name, Target: s.target})
	f := front.New(fo)
	closeFront := frontwire.Closer(f, plan, audit)

	ctx, cancel := context.WithCancel(context.Background())
	served := make(chan struct{})
	go func() {
		defer close(served)
		ready := func(a net.Addr) {
			fmt.Fprintf(out, "flyball: serving rig %s on %s (%s)\n", name, describeListen(plan, a), plan.Shape)
			if onListen != nil {
				onListen(a)
			}
		}
		if err := frontwire.Serve(ctx, plan, f, ready); err != nil {
			// A front that cannot listen does not stop the rig (D-028):
			// it runs on, stoppable by signal or `flyball stop`.
			fmt.Fprintln(out, "flyball: the front cannot serve:", err)
		}
	}()
	go func() {
		for presses := 1; ; presses++ {
			var sig os.Signal
			select {
			case sig = <-sigs:
			case <-ctx.Done():
				return
			}
			if sig == nil {
				return
			}
			s.escalate(presses, sig)
		}
	}()

	err = s.run()
	cancel()
	<-served
	closeFront()
	return err
}

// onListen, if set, is told where the front listens (tests: port 0).
var onListen func(net.Addr)

func describeListen(p front.Plan, a net.Addr) string {
	if a.Network() == "unix" {
		return "unix:" + a.String()
	}
	scheme := "http"
	if p.TLS != nil {
		scheme = "https"
	}
	return scheme + "://" + a.String() + "/"
}

// runnerExec builds each incarnation's command: flyball-runner bare, or
// via uv -- either way in its own process group (D-038), the same as
// flyballd's ProcessBackend: uv ignores SIGINT, leaving it to the
// terminal, and a bare runner would share the terminal's group and die
// with it on a hangup; stopped from anywhere else the runner would never
// hear it either, so a stop goes to the group (supervisor.forward).
// stdout/stderr are where the runner's own output goes (run's log tee);
// stdin is not the terminal -- nothing the runner does needs it.
func runnerExec(useUV bool, rig string, args []string, stdout, stderr io.Writer) func() *exec.Cmd {
	return func() *exec.Cmd {
		var cmd *exec.Cmd
		if useUV {
			uvArgs := append([]string{"run", "--project", filepath.Dir(rig), "flyball-runner"}, args...)
			cmd = exec.Command(uvCommand, uvArgs...)
		} else {
			cmd = exec.Command(runnerCommand, args...)
		}
		cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		cmd.Stdout, cmd.Stderr = stdout, stderr
		return cmd
	}
}

// flyball-runner's exit codes that end a run (entrypoint.py).
const (
	exitBadConfig = 2 // a bad rig file, or a runner too old to know --front-dir
	exitRigBusy   = 3 // another runner holds the rig's <store>.lock
	exitFrontDir  = 4 // the front-dir is unsafe or incomplete: rewrite, respawn once
	// 5, it could not serve (socket or server), is a crash: started again.
)

// supervisor runs one rig's runner for `flyball run`: each incarnation gets
// the front-dir rewritten with a fresh key, and a crash is followed by
// another incarnation (1 s backoff, doubling to 30 s, back to 1 s after
// 10 s up). A small copy of the ProcessBackend's rules, kept in the
// foreground: the runner's own stdout/stderr are wired straight to
// run.go's log tee (runnerExec); out is where this type's own lines go,
// the same tee.
type supervisor struct {
	dir, aud, rig string
	ep            endpoint.Endpoint
	command       func() *exec.Cmd
	minBackoff    time.Duration
	wake          chan struct{}
	out           io.Writer

	mu       sync.Mutex
	key      [32]byte
	alive    bool
	cmd      *exec.Cmd
	stopping bool
}

// target is where the front proxies: the live incarnation.
func (s *supervisor) target(context.Context) (front.Target, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case s.alive:
		return front.Target{Endpoint: s.ep, Aud: s.aud, Key: s.key}, nil
	case s.stopping:
		return front.Target{}, front.ErrNotRunning
	}
	return front.Target{}, front.ErrStarting
}

// signal stops the run: sig goes to the runner (its group under uv), and
// no incarnation follows.
func (s *supervisor) signal(sig os.Signal) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.stopping = true
	if s.alive {
		s.forward(sig)
	}
	select {
	case s.wake <- struct{}{}:
	default:
	}
}

// escalate is the press'th stop signal (D-045): the first, sig, stops the
// run; the second is SIGINT again; the third and later SIGKILL the
// runner's group.
func (s *supervisor) escalate(press int, sig os.Signal) {
	switch press {
	case 1:
		s.mu.Lock()
		pid := 0
		if s.alive {
			pid = s.cmd.Process.Pid
		}
		s.mu.Unlock()
		if pid != 0 {
			fmt.Fprintf(s.out, "flyball: stopping... (Ctrl-C again to hurry, a third time kills pid %d)\n", pid)
		} else {
			fmt.Fprintln(s.out, "flyball: stopping...")
		}
		s.signal(sig)
	case 2:
		fmt.Fprintln(s.out, "flyball: hurrying the runner (SIGINT again); Ctrl-C once more kills it")
		s.signal(syscall.SIGINT)
	default:
		s.mu.Lock()
		pid := 0
		if s.alive {
			pid = s.cmd.Process.Pid
		}
		s.mu.Unlock()
		if pid != 0 {
			fmt.Fprintf(s.out, "flyball: killing the runner (SIGKILL to pid %d's group); its recording may not be closed cleanly\n", pid)
		}
		s.signal(syscall.SIGKILL)
	}
}

// forward sends sig to the live incarnation. s.mu held.
func (s *supervisor) forward(sig os.Signal) {
	if s.cmd.SysProcAttr != nil && s.cmd.SysProcAttr.Setpgid {
		if ss, ok := sig.(syscall.Signal); ok {
			syscall.Kill(-s.cmd.Process.Pid, ss)
			return
		}
	}
	s.cmd.Process.Signal(sig)
}

func (s *supervisor) run() error {
	backoff := s.minBackoff
	retried := false // an exit 4 has had its respawn
	for {
		key, err := frontdir.Write(s.dir, s.aud, s.ep)
		if errors.Is(err, frontdir.ErrLive) {
			lock := filepath.Join(s.dir, frontdir.Lock)
			if pid, perr := pidFromLockFile(lock); perr == nil {
				return fmt.Errorf("this rig is already running as pid %d (%s): end that run first -- Ctrl-C in its terminal, or `kill %d`"+
					" (`flyball stop %s` stops the rig, not the runner)", pid, lock, pid, s.rig)
			}
			return fmt.Errorf("a runner already holds %s in %s: is this rig already running under `flyball run`?", frontdir.Lock, s.dir)
		}
		if err != nil {
			return err
		}
		cmd := s.command()
		s.mu.Lock()
		if s.stopping {
			s.mu.Unlock()
			return nil
		}
		if err := cmd.Start(); err != nil {
			s.mu.Unlock()
			return fmt.Errorf("starting flyball-runner: %w", err)
		}
		s.key, s.cmd, s.alive = key, cmd, true
		s.mu.Unlock()

		started := time.Now()
		err = cmd.Wait()
		s.mu.Lock()
		s.alive = false
		stopping := s.stopping
		s.mu.Unlock()
		if stopping {
			// The stop's own SIGINT/SIGTERM ending it is a clean stop; a
			// SIGKILL (the third press) or any other signal is not.
			if sig, ok := killedBy(cmd.ProcessState); ok && sig != syscall.SIGINT && sig != syscall.SIGTERM {
				return errRunnerKilled{sig}
			}
			return nil
		}
		code := cmd.ProcessState.ExitCode()
		up := time.Since(started) > 10*time.Second
		switch {
		case err == nil:
			return nil
		case code == exitBadConfig:
			return fmt.Errorf("flyball-runner: %w (a bad rig file, or a flyball-runner too old for --front-dir)", err)
		case code == exitRigBusy:
			return fmt.Errorf("flyball-runner: %w: the rig is busy -- another runner holds its store", err)
		case code == exitFrontDir && (!retried || up):
			retried = true
			fmt.Fprintf(s.out, "flyball: the runner refused its front-dir %s (exit 4); rewriting it and starting it again\n", s.dir)
			continue
		case code == exitFrontDir:
			return fmt.Errorf("flyball-runner: %w: it refused its front-dir %s twice", err, s.dir)
		}
		if up {
			backoff = s.minBackoff
		}
		fmt.Fprintf(s.out, "flyball: the runner stopped (%v); starting it again in %s\n", err, backoff)
		select {
		case <-time.After(backoff):
		case <-s.wake:
		}
		if backoff < 30*time.Second {
			backoff *= 2
		}
	}
}

func randomHex(n int) string {
	b := make([]byte, n)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// A run that ended with its runner killed rather than stopped -- the
// third Ctrl-C's SIGKILL, or any signal but the stop's own SIGINT/SIGTERM
// -- exits 128+N, N the signal (137 for SIGKILL), as a shell reports a
// process a signal ended: a wrapper can tell it from a clean stop (0), and
// no number of flyball's or flyball-runner's own means something else
// (book/src/7-reference/cli.md#exit-codes). Its recording may not have
// been closed cleanly.

// errRunnerKilled: the run ended with the runner killed by sig (exit 128+sig).
type errRunnerKilled struct{ sig syscall.Signal }

func (e errRunnerKilled) Error() string {
	return fmt.Sprintf("the runner was killed (%v), not stopped: its recording may not have been closed cleanly", e.sig)
}

// runExitCode is flyball's exit code for runDirect's result: 0, 1, or
// 128+N for a runner killed by signal N.
func runExitCode(err error) int {
	var killed errRunnerKilled
	switch {
	case err == nil:
		return 0
	case errors.As(err, &killed):
		return 128 + int(killed.sig)
	}
	return 1
}

// killedBy is the signal that ended ps: its own death by a signal, or --
// under uv, which exits 128+N when its child dies of signal N -- an exit
// code above 128.
func killedBy(ps *os.ProcessState) (syscall.Signal, bool) {
	if ws, ok := ps.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
		return ws.Signal(), true
	}
	if code := ps.ExitCode(); code > 128 && code < 128+65 {
		return syscall.Signal(code - 128), true
	}
	return 0, false
}
