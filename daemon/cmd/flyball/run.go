package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"flyballd/internal/exposure"
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
// in front of it (see serve_ui.go). On an address beyond loopback it
// serves nothing until the runner answers GET /api/auth. An auth
// misconfiguration removes exposure, never operation: an open runner (no
// password, no token) keeps running and the UI is served on 127.0.0.1 on
// the same port instead, with one warning saying why, unless
// --insecure-open or FLYBALL_INSECURE_OPEN=1 (per run: never a rig-file
// key) says to serve it where asked. A runner with credentials is served
// with a warning that plain HTTP carries them in the clear.
//
// --uv runs `flyball-runner` via `uv run --project <dir>` instead of
// execing it bare, where <dir> is the rig file's own directory -- the
// bare exec only works when flyball-runner happens to already be on
// $PATH, which it never is outside an app's own uv-managed venv
// (examples/humidity's, examples/furnace's, ...). `uv run` finds that
// venv from --project the same way it would from cwd if you'd `cd`ed
// there yourself.
// runnerCommand is what runDirect execs when not going through uv; a
// variable so a test can stand a fake runner in for it.
var runnerCommand = "flyball-runner"

func runDirect(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: flyball run <rig-file> [--serve-ui ADDR] [--uv] [flyball-runner flags...]")
	}

	// args[0] is known present (the guard above), so it's safe to load it now
	// for `runner.run` defaults -- this must stay after that guard, not
	// before it, since reading the rig file obviously requires one.
	runner := runnerSection(args[0])
	defaults, _ := runner["run"].(map[string]any)
	runnerPort, _ := asNonEmptyString(runner["port"])
	serveAddr, wantUI, port, useUV, args := resolveRunFlags(args, defaults, runnerPort)
	if len(args) < 1 {
		return fmt.Errorf("usage: flyball run <rig-file> [--serve-ui ADDR] [--uv] [flyball-runner flags...]")
	}
	insecureOpen := insecureOpenRequested(args)

	var cmd *exec.Cmd
	if useUV {
		projectDir := filepath.Dir(args[0])
		uvArgs := append([]string{"run", "--project", projectDir, "flyball-runner"}, args...)
		cmd = exec.Command("uv", uvArgs...)
	} else {
		cmd = exec.Command(runnerCommand, args...)
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
	uiDone := make(chan struct{})
	if !wantUI {
		close(uiDone)
	} else {
		go func() {
			defer close(uiDone)
			var plan *exposure.Plan
			if !exposure.IsLoopback(serveAddr) {
				// Beyond loopback the front serves nothing until the runner
				// has said what door it has: an open one gets loopback only.
				p, err := guardExposure(uiCtx, serveAddr, port, insecureOpen)
				if err != nil {
					return // the runner exited first
				}
				plan = &p
				serveAddr = p.Addr
			}
			fmt.Fprintf(os.Stderr, "flyball: serving UI on %s, proxying to runner on 127.0.0.1:%s\n", serveAddr, port)
			if err := serveUI(uiCtx, serveAddr, port, plan); err != nil {
				fmt.Fprintln(os.Stderr, "flyball: UI server:", err)
			}
		}()
	}

	err := cmd.Wait()
	cancelUI() // the runner is gone: stop the front, and wait for it
	<-uiDone
	return err
}

// guardExposure waits for the runner on 127.0.0.1:port to answer
// GET /api/auth, then plans the front on addr (exposure.Decide), logging
// its warning. A runner whose door cannot be read counts as open. Returns
// ctx's error if the runner exits first.
func guardExposure(ctx context.Context, addr, port string, insecureOpen bool) (exposure.Plan, error) {
	fmt.Fprintf(os.Stderr, "flyball: waiting for the runner on 127.0.0.1:%s before serving the UI on %s\n", port, addr)
	url := "http://127.0.0.1:" + port + "/api/auth"
	client := &http.Client{Timeout: 2 * time.Second}
	for {
		door, err := exposure.Probe(ctx, client, url)
		if err == nil || errors.Is(err, exposure.ErrNotADoor) {
			// It answered; if not as a runner's door, it cannot tell: open.
			plan := exposure.Decide(addr, door, insecureOpen)
			if plan.Warning != "" {
				fmt.Fprintln(os.Stderr, "flyball: WARNING:", plan.Warning)
			}
			return plan, nil
		}
		// No answer yet: the runner is still starting.
		select {
		case <-ctx.Done():
			return exposure.Plan{}, ctx.Err()
		case <-time.After(250 * time.Millisecond):
		}
	}
}

// insecureOpenRequested is the explicit opt-in to serve an open runner
// beyond loopback, per run only: --insecure-open among the runner's flags
// (left there, so the runner sees it too), or FLYBALL_INSECURE_OPEN set to
// 1/true/yes/on (inherited by the runner). Never a rig-file key: a file
// can be pasted from anywhere, and `extends` would inherit it.
func insecureOpenRequested(args []string) bool {
	for _, a := range args {
		if a == "--insecure-open" {
			return true
		}
	}
	switch strings.ToLower(strings.TrimSpace(os.Getenv("FLYBALL_INSECURE_OPEN"))) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
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
	run, _ := runnerSection(rigPath)["run"].(map[string]any)
	return run
}

// runnerSection is rigPath's `runner` map, `extends` resolved; nil if the
// file cannot be loaded or has none.
func runnerSection(rigPath string) map[string]any {
	document, _, err := rigfile.ResolveLayers([]string{rigPath}, nil)
	if err != nil {
		return nil
	}
	runner, _ := document["runner"].(map[string]any)
	return runner
}

// resolveRunFlags pops --serve-ui/--uv out of args (which may appear
// anywhere, same as popValue/popBool always allowed), falling back to
// defaults (runner.run's serve_ui/port/uv, as loaded by runYAMLDefaults)
// for any not explicitly given on the command line. CLI flags always win:
// a YAML value only ever supplies the default for a flag whose CLI form
// was absent. Pure and independent of any file I/O, so it's unit-testable
// without a real rig file.
//
// port is where the runner serves, so where the UI proxies to: --port,
// else runnerPort (the rig file's own runner.port), else runner.run.port,
// else 8000. --port stays in rest, and a runner.run.port is added to it,
// so the runner serves where the front proxies; runner.port and the
// default are the runner's own already.
func resolveRunFlags(args []string, defaults map[string]any, runnerPort string) (serveAddr string, wantUI bool, port string, useUV bool, rest []string) {
	serveAddr, args, explicitUI := popValue(args, "--serve-ui")
	wantUI = explicitUI
	if !explicitUI {
		if v, ok := asNonEmptyString(defaults["serve_ui"]); ok {
			serveAddr, wantUI = v, true
		}
	}

	port, args, explicitPort := popValue(args, "--port")
	switch {
	case explicitPort:
		args = append(args, "--port", port)
	case runnerPort != "":
		port = runnerPort
	default:
		if v, ok := asNonEmptyString(defaults["port"]); ok {
			port = v
			args = append(args, "--port", port)
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
