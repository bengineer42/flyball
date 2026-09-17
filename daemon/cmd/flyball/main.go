// Command flyball is the CLI client, per plan.md's "CLI addressing"
// section and its "Language" section's decision to rewrite it in Go
// alongside the daemon, sharing internal/client with it.
//
// This is a first pass: the addressing/resolution logic plan.md actually
// designed (-s/--server, FLYBALL_URL vs FLYBALLD_URL, default-runner
// precedence, daemon-crash fallback) is implemented for real. The full
// schema-driven command set today's Python cli.py builds dynamically
// from a rig's schema is NOT reimplemented here yet -- read/demand/status
// are illustrative commands proving the addressing works end to end, not
// the complete command surface. That's real remaining work, not an
// oversight.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"flyballd/internal/client"
)

func main() {
	server := flag.String("s", "", "runner identity, daemon-routed (also --server)")
	flag.StringVar(server, "server", "", "runner identity, daemon-routed")
	flag.Parse()

	args := flag.Args()
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: flyball [-s NAME] <run|read|demand|status> ...")
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

	target, err := resolveTarget(*server)
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

func runCommand(t client.Target, args []string) error {
	switch args[0] {
	case "read":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball read <address>")
		}
		var out any
		if err := t.Do("GET", "/api/read/"+args[1], nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "demand":
		if len(args) != 3 {
			return fmt.Errorf("usage: flyball demand <address> <value>")
		}
		body, err := json.Marshal(map[string]any{"value": args[2]})
		if err != nil {
			return err
		}
		var out any
		if err := t.Do("PUT", "/api/signals/"+args[1], bytes.NewReader(body), &out); err != nil {
			return err
		}
		return printJSON(out)

	case "status":
		var out any
		if err := t.Do("GET", "/api/status", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func printJSON(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}
