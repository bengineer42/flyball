// Command flyball is the CLI client, per plan.md's "CLI addressing"
// section and its "Language" section's decision to rewrite it in Go
// alongside the daemon, sharing internal/client with it.
//
// Capability parity with the old Python cli.py (engine/src/flyball/cli.py):
// every HTTP-addressed capability is here (read/demand/watch/status/waits/
// wait/clock/schema/devices/controllers/device view+invoke/sessions/
// export/program */sim */logs), plus daemon-management commands the
// Python CLI never had (it predates the Go daemon). `rig check`, `rig
// schema` and `program schema` are all local operations reimplemented
// against the embedded, checked-in JSON Schemas (daemon/internal/schema),
// generated from the Python RigConfig model / dialect module but not
// requiring Python at runtime (rig.go, program_schema.go). (`password`
// and `new` are local too -- see local.go.)
//
// Dynamic per-device argparse flags (schema -> --dotted-flag, per
// cli.py's "Schema -> argparse" region) are NOT reimplemented either --
// `flyball run <device> <command> [KEY=VALUE|JSON ...]` takes positional
// KEY=VALUE pairs (or a single raw JSON object) instead. Equivalent
// capability (you can still call any device command with any arguments),
// different, simpler shape, per the task's explicit allowance for Go
// idioms over an exact port.
//
// `password` and `new` (local.go) ARE ported despite the note above having
// once said otherwise: both are pure local operations (scrypt hashing,
// text templating) with no dependency on Python at runtime, so they're
// reimplemented directly rather than shelling out.
package main

import (
	"fmt"
	"io"
	"os"

	"flyballd/internal/client"
)

func main() {
	server := flagServer()
	token := client.Token(flagToken())
	args := restArgs()

	// --help/-h anywhere in the command line is a deliberate, successful
	// request for usage (exit 0, stdout) -- distinct from no args at all,
	// which is a usage error (exit 2, stderr, below). People expect the
	// flag to work regardless of position, same as -s/--token.
	if help, rest := popBool(args, "--help"); help {
		args = rest
		usage(os.Stdout)
		os.Exit(0)
	}
	if help, rest := popBool(args, "-h"); help {
		args = rest
		usage(os.Stdout)
		os.Exit(0)
	}

	if len(args) == 0 {
		usage(os.Stderr)
		os.Exit(2)
	}

	// `run` starts a rig's runner directly -- no daemon involved, no
	// target to resolve, per Ben's word: the CLI shouldn't require the
	// daemon just to run one rig. Checked before addressing resolution
	// since there's nothing to address yet.
	if args[0] == "run" {
		if err := runDirect(args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `rig ...` is a local operation against the rig file/schema itself,
	// not a request to a running runner -- dispatched before target
	// resolution for the same reason `run`/`daemon`/`logs` are.
	if args[0] == "rig" {
		if err := runRigCommand(args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `daemon ...` talks to flyballd's own API (start/stop/restart/list
	// runners), never routed through a runner -- distinct addressing from
	// everything else, so it's dispatched before target resolution too.
	if args[0] == "daemon" {
		if err := runDaemonCommand(args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `logs` is also daemon-addressed (interface.md's GET
	// /api/runners/{name}/logs is the daemon's own endpoint, not
	// pass-through routing), so it's dispatched the same way, before -s
	// resolution -- -s picks a runner behind the daemon's proxy, which
	// isn't what a log fetch wants.
	if args[0] == "logs" {
		if err := runLogsCommand(args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `password` and `new` are local operations (local.go) -- no runner
	// or daemon involved, so dispatched before target resolution too.
	if args[0] == "password" {
		if err := runPasswordCommand(args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}
	if args[0] == "new" {
		if err := runNewCommand(args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `program schema` is local too -- it only ever prints the embedded
	// program schema (daemon/internal/schema), no runner involved. Every
	// other `program ...` subcommand (check/run/status/stop) still talks
	// to a runner, so only this one is intercepted here.
	if args[0] == "program" && len(args) > 1 && args[1] == "schema" {
		if err := runProgramSchemaCommand(args[2:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `login`/`logout` sign in/out of a front's password door
	// (daemon/internal/front/auth.go) -- login resolves its own target
	// (an explicit URL argument, or the usual -s/FLYBALLD_URL/FLYBALL_URL
	// precedence), since it may need to reach a front before any token
	// exists to resolve-and-authenticate with.
	if args[0] == "login" {
		if err := runLoginCommand(server, args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}
	if args[0] == "logout" {
		if err := runLogoutCommand(server); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `token ...` manages the named-tokens file offline, the same file a
	// running front reads (daemon/internal/front/store.Tokens re-reads on
	// stat change) -- no runner or daemon request at all, so dispatched
	// before target resolution like `rig`/`daemon`.
	if args[0] == "token" {
		if err := runTokenCommand(args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	// `stop` tries the front first (POST /api/rig/stop) and falls back to
	// SIGUSR1 when it can't be reached, so it resolves its own target
	// rather than sharing the generic block below (a stop must still work
	// when that resolution, or the front itself, is unreachable).
	if args[0] == "stop" {
		if err := runStopCommand(server, token, args[1:]); err != nil {
			fmt.Fprintln(os.Stderr, "flyball:", err)
			os.Exit(1)
		}
		return
	}

	target, err := resolveTarget(server)
	if err != nil {
		fmt.Fprintln(os.Stderr, "flyball:", err)
		os.Exit(1)
	}
	target = target.WithToken(token)

	if err := runCommand(target, args); err != nil {
		fmt.Fprintln(os.Stderr, "flyball:", err)
		os.Exit(1)
	}
}

// resolveTarget implements plan.md's precedence: explicit -s wins; else
// if FLYBALLD_URL is set (a daemon is in play) use the default-runner
// rules; else FLYBALL_URL alone, direct-to-runner, unchanged from today.
func resolveTarget(server string) (client.Target, error) {
	if server != "" {
		return client.Resolve(server)
	}
	if daemonURL := os.Getenv("FLYBALLD_URL"); daemonURL != "" {
		return client.ResolveDefault(daemonURL)
	}
	return client.Resolve("")
}

func usage(w io.Writer) {
	fmt.Fprint(w, `usage: flyball [-s NAME] [--token TOKEN] <command> ...

runner commands (addressed via -s/--server, FLYBALL_URL or FLYBALLD_URL):
  login [URL] [--scope SCOPE]...       admin password -> a saved named token (prompted, never on argv)
  logout                              drop the saved token (locally only; see token revoke)
  stop [NAME] [--pid N] [--front-dir DIR] [--reason TEXT]
                                      POST /api/rig/stop; SIGUSR1 if the front can't be reached
  stop --all [--reason TEXT]          the rig stop on every rig flyballd lists (needs operate on each);
                                      runners stay up; non-zero if any stop was refused or failed
  read ADDRESS [--fresh]              GET /api/read/{address}
  demand ADDRESS VALUE                PUT /api/signals/{address}
  status [--json]                     one screen: devices, controllers, waits
  schema                              the rig's schema
  clock                               the rig's timebase
  waits                               what the rig is waiting on
  wait fire|interrupt NAME            answer or cancel a wait
  watch samples|controllers|writes|signals   follow a live stream
  devices / controllers               list them
  view DEVICE                         one device's tree
  device-schema DEVICE                one device's schema
  invoke DEVICE COMMAND [KEY=VALUE ...]   run a device command
  sessions                            list recorded sessions
  export SESSION [--format csv|json|zip] [--out PATH]
                                      a session's data, as the runner exports it
  program check|run|status|stop PATH  program files
  sim show|clock|step|set|reset|config|save   a simulated rig's knobs

local (no runner or daemon involved):
  rig schema                          the rig file's JSON Schema, for an editor
  run RIG-FILE [--listen ADDR] [--uv] [--insecure-open] [flyball-runner flags...]   start a runner directly, foreground
  password [PASSWORD]                 hash a password for runner.auth.password
  new NAME [--dir PATH]                write a starting point for a device driver
  token create --name N --config PATH [--scope S ...] [--kind human|service|agent] [--expires D]
                                      write a token into the front's tokens.json offline; prints it once
  token list --config PATH            list tokens (never their secrets)
  token revoke ID --config PATH       remove a token; the front picks this up at its next check

daemon-managed (talks to flyballd via FLYBALLD_URL, never routed through a runner):
  daemon runners                      list registered runners
  daemon start MANIFEST.json          POST /api/runners
  daemon stop NAME                    DELETE /api/runners/{name}
  daemon restart NAME                 POST /api/runners/{name}/restart
  logs NAME                           GET /api/runners/{name}/logs
`)
}
