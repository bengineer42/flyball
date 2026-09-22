package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"
)

// runDirect starts a runner directly, no daemon involved at all -- the
// CLI itself execs the literal `flyball-runner` command (Python's own
// entry point, renamed from `flyball-daemon` per the daemon/runner
// rename) in the foreground, same invocation plan.md's "How the daemon
// starts and talks to a runner" section already specifies. No
// supervision (no restart-on-crash), no registry, no routing -- those
// are what you lose by not going through flyballd; this is the "you
// shouldn't need the daemon to run one runner" escape hatch.
//
// --serve-ui ADDR additionally serves the embedded dashboard UI on ADDR,
// reverse-proxying /api, /ws and /mcp to the runner -- so the runner is
// reachable through the CLI's own binary with no separate reverse proxy
// in front of it (see serve_ui.go).
//
// --uv runs `flyball-runner` via `uv run --project <dir>` instead of
// execing it bare, where <dir> is the rig file's own directory -- the
// bare exec only works when flyball-runner happens to already be on
// $PATH, which it never is outside an app's own uv-managed venv
// (examples/humidity's, examples/furnace's, ...). `uv run` finds that
// venv from --project the same way it would from cwd if you'd `cd`ed
// there yourself.
func runDirect(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: flyball run <rig-file> [--serve-ui ADDR] [--uv] [flyball-runner flags...]")
	}

	serveAddr, args, wantUI := popValue(args, "--serve-ui")

	port, _, ok := popValue(args, "--port")
	if !ok {
		port = "8000"
	}

	useUV, args := popBool(args, "--uv")

	var cmd *exec.Cmd
	if useUV {
		projectDir := filepath.Dir(args[0])
		uvArgs := append([]string{"run", "--project", projectDir, "flyball-runner"}, args...)
		cmd = exec.Command("uv", uvArgs...)
	} else {
		cmd = exec.Command("flyball-runner", args...)
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("starting flyball-runner: %w", err)
	}

	// Forward Ctrl+C / SIGTERM to the child so it can shut down cleanly,
	// rather than the CLI exiting and leaving it orphaned mid-signal. The
	// child (uvicorn) already prints its own graceful-shutdown sequence
	// once the signal reaches it, but that can take a moment (draining
	// connections, stopping polling) -- print immediately, at the instant
	// the signal is caught, so Ctrl+C gets visible feedback right away
	// rather than a silent pause before the child's own logs show up.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, os.Interrupt, syscall.SIGTERM)
	go func() {
		sig := <-sigs
		fmt.Fprintln(os.Stderr, "flyball: stopping...")
		signal.Stop(sigs) // a second Ctrl+C kills the process the normal way, doesn't hang
		_ = cmd.Process.Signal(sig)
	}()

	uiCtx, cancelUI := context.WithCancel(context.Background())
	defer cancelUI()
	if wantUI {
		fmt.Fprintf(os.Stderr, "flyball: serving UI on %s, proxying to runner on 127.0.0.1:%s\n", serveAddr, port)
		go func() {
			if err := serveUI(uiCtx, serveAddr, port); err != nil {
				fmt.Fprintln(os.Stderr, "flyball: UI server:", err)
			}
		}()
	}

	return cmd.Wait()
}
