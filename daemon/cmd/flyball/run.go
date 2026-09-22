package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
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
func runDirect(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: flyball run <rig-file> [--serve-ui ADDR] [flyball-runner flags...]")
	}

	serveAddr, args, wantUI := popValue(args, "--serve-ui")

	port, _, ok := popValue(args, "--port")
	if !ok {
		port = "8000"
	}

	cmd := exec.Command("flyball-runner", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("starting flyball-runner: %w", err)
	}

	// Forward Ctrl+C / SIGTERM to the child so it can shut down cleanly,
	// rather than the CLI exiting and leaving it orphaned mid-signal.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, os.Interrupt, syscall.SIGTERM)
	go func() {
		sig := <-sigs
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
