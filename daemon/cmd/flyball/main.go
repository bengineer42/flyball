// Command flyball is the CLI client, per plan.md's "CLI addressing"
// section and its "Language" section's decision to rewrite it in Go
// alongside the daemon, sharing internal/client with it.
//
// Capability parity with the old Python cli.py (engine/src/flyball/cli.py):
// every HTTP-addressed capability is here (read/demand/watch/status/waits/
// wait/clock/schema/devices/controllers/device view+invoke/sessions/
// export/program */sim */logs), plus daemon-management commands the
// Python CLI never had (it predates the Go daemon). Deliberately NOT
// reimplemented:
// `rig check`, `rig schema`, `program schema`, `password`, `new` -- these
// are local operations against Python's own rig-config/dialect/scaffold
// code, not requests to a running runner at all, so they don't fit this
// client/addressing model and still need the flyball Python package
// installed either way. See the handoff notes for where that gap is left.
//
// Dynamic per-device argparse flags (schema -> --dotted-flag, per
// cli.py's "Schema -> argparse" region) are NOT reimplemented either --
// `flyball run <device> <command> [KEY=VALUE|JSON ...]` takes positional
// KEY=VALUE pairs (or a single raw JSON object) instead. Equivalent
// capability (you can still call any device command with any arguments),
// different, simpler shape, per the task's explicit allowance for Go
// idioms over an exact port.
package main

import (
	"fmt"
	"os"

	"flyballd/internal/client"
)

func main() {
	server := flagServer()
	args := restArgs()

	if len(args) == 0 {
		usage()
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

	target, err := resolveTarget(server)
	if err != nil {
		fmt.Fprintln(os.Stderr, "flyball:", err)
		os.Exit(1)
	}

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

func usage() {
	fmt.Fprint(os.Stderr, `usage: flyball [-s NAME] <command> ...

runner commands (addressed via -s/--server, FLYBALL_URL or FLYBALLD_URL):
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
  export SESSION [--out PATH]         a session as Bluesky documents
  program check|run|status|stop PATH  program files
  sim show|clock|step|set|reset|config|save   a simulated rig's knobs

no-daemon:
  run RIG-FILE [flyball-runner flags...]   start a runner directly, foreground

daemon-managed (talks to flyballd via FLYBALLD_URL, never routed through a runner):
  daemon runners                      list registered runners
  daemon start MANIFEST.json          POST /api/runners
  daemon stop NAME                    DELETE /api/runners/{name}
  daemon restart NAME                 POST /api/runners/{name}/restart
  logs NAME                           GET /api/runners/{name}/logs
`)
}
