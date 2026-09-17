package main

import (
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
)

// runDirect starts a runner directly, no daemon involved at all -- the
// CLI itself execs the literal `flyball-daemon` command in the
// foreground, same invocation plan.md's "How the daemon starts and
// talks to a runner" section already specifies. No supervision (no
// restart-on-crash), no registry, no routing -- those are what you lose
// by not going through flyballd; this is the "you shouldn't need the
// daemon to run one runner" escape hatch.
func runDirect(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: flyball run <rig-file> [flyball-daemon flags...]")
	}

	cmd := exec.Command("flyball-daemon", args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("starting flyball-daemon: %w", err)
	}

	// Forward Ctrl+C / SIGTERM to the child so it can shut down cleanly,
	// rather than the CLI exiting and leaving it orphaned mid-signal.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, os.Interrupt, syscall.SIGTERM)
	go func() {
		sig := <-sigs
		_ = cmd.Process.Signal(sig)
	}()

	return cmd.Wait()
}
