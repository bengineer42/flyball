package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"syscall"

	"flyballd/internal/rigfile"
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

	// args[0] is known present (the guard above), so it's safe to load it now
	// for `runner.run` defaults -- this must stay after that guard, not
	// before it, since reading the rig file obviously requires one.
	defaults := runYAMLDefaults(args[0])
	serveAddr, wantUI, port, useUV, args := resolveRunFlags(args, defaults)
	if len(args) < 1 {
		return fmt.Errorf("usage: flyball run <rig-file> [--serve-ui ADDR] [--uv] [flyball-runner flags...]")
	}

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

// runYAMLDefaults reads rigPath's own document -- `extends` resolved the
// same way `flyball rig check` resolves it -- and returns its
// `runner.run` map, or nil if the file can't be loaded or the key is
// absent (most rigs won't set it). These are Go-CLI-only defaults for
// `flyball run`'s own flags (`serve_ui`, `uv`, `port`): the Python side
// (`RunnerConfig.run` in engine/src/flyball/runtime/config.py) accepts
// this key but never reads or validates its contents, so any load error
// here is left for `flyball-runner` itself to report properly once it
// loads the rig file for real -- this best-effort read must never be
// the thing that turns a bad rig file into a confusing error.
func runYAMLDefaults(rigPath string) map[string]any {
	document, _, err := rigfile.ResolveLayers([]string{rigPath}, nil)
	if err != nil {
		return nil
	}
	runner, _ := document["runner"].(map[string]any)
	run, _ := runner["run"].(map[string]any)
	return run
}

// resolveRunFlags pops --serve-ui/--port/--uv out of args (which may
// appear anywhere, same as popValue/popBool always allowed), falling back
// to defaults (runner.run's serve_ui/port/uv, as loaded by
// runYAMLDefaults) for any of the three not explicitly given on the
// command line. CLI flags always win: a YAML value only ever supplies the
// default for a flag whose CLI form was absent. Pure and independent of
// any file I/O, so it's unit-testable without a real rig file.
func resolveRunFlags(args []string, defaults map[string]any) (serveAddr string, wantUI bool, port string, useUV bool, rest []string) {
	serveAddr, args, explicitUI := popValue(args, "--serve-ui")
	wantUI = explicitUI
	if !explicitUI {
		if v, ok := asNonEmptyString(defaults["serve_ui"]); ok {
			serveAddr, wantUI = v, true
		}
	}

	port, args, explicitPort := popValue(args, "--port")
	if !explicitPort {
		if v, ok := asNonEmptyString(defaults["port"]); ok {
			port = v
		} else {
			port = "8000"
		}
	}

	explicitUV, args := popBool(args, "--uv")
	useUV = explicitUV
	if !explicitUV {
		if v, ok := defaults["uv"].(bool); ok && v {
			useUV = true
		}
	}

	return serveAddr, wantUI, port, useUV, args
}

// asNonEmptyString reads a YAML-decoded scalar as a string, matching the
// forms `runner.run`'s own values can take (a YAML string for serve_ui, a
// string or a bare number for port). "" and absent both count as not set.
func asNonEmptyString(v any) (string, bool) {
	switch t := v.(type) {
	case string:
		return t, t != ""
	case int:
		return strconv.Itoa(t), true
	case int64:
		return strconv.FormatInt(t, 10), true
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64), true
	default:
		return "", false
	}
}
