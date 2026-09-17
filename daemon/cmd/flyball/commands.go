// Runner-addressed commands: everything that goes through client.Target
// (direct to a runner via FLYBALL_URL, or daemon-routed via -s/
// FLYBALLD_URL). Mirrors cli.py's command surface -- see main.go's doc
// comment for what's deliberately not ported.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"

	"flyballd/internal/client"
	"flyballd/internal/wsclient"
)

func runCommand(t client.Target, args []string) error {
	switch args[0] {

	case "read":
		fresh, rest := popBool(args[1:], "--fresh")
		if len(rest) != 1 {
			return fmt.Errorf("usage: flyball read <address> [--fresh]")
		}
		path := "/api/read/" + rest[0]
		if fresh {
			path += "?fresh=true"
		}
		var out any
		if err := t.Do("GET", path, nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "demand":
		if len(args) != 3 {
			return fmt.Errorf("usage: flyball demand <address> <value>")
		}
		body, err := json.Marshal(jsonOrString(args[2]))
		if err != nil {
			return err
		}
		var out any
		if err := t.Do("PUT", "/api/signals/"+args[1], bytes.NewReader(body), &out); err != nil {
			return err
		}
		return printJSON(out)

	case "status":
		asJSON, rest := popBool(args[1:], "--json")
		_ = rest
		var health any
		if err := t.Do("GET", "/api/health", nil, &health); err != nil {
			return err
		}
		if asJSON {
			return printJSON(health)
		}
		return printStatus(t, health)

	case "schema":
		var out any
		if err := t.Do("GET", "/api/schema", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "clock":
		var out any
		if err := t.Do("GET", "/api/clock", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "waits":
		var out any
		if err := t.Do("GET", "/api/waits", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "wait":
		if len(args) != 3 || (args[1] != "fire" && args[1] != "interrupt") {
			return fmt.Errorf("usage: flyball wait fire|interrupt <name>")
		}
		var out any
		if err := t.Do("POST", "/api/waits/"+args[2]+"/"+args[1], nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "watch":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball watch samples|controllers|writes|signals")
		}
		return watchStream(t, args[1])

	case "devices":
		var out any
		if err := t.Do("GET", "/api/devices", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "controllers":
		var out any
		if err := t.Do("GET", "/api/controllers", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "view":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball view <device>")
		}
		var out any
		if err := t.Do("GET", "/api/devices/"+args[1], nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "device-schema":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball device-schema <device>")
		}
		var schema any
		if err := t.Do("GET", "/api/schema", nil, &schema); err != nil {
			return err
		}
		m, _ := schema.(map[string]any)
		devices, _ := m["devices"].(map[string]any)
		dev, ok := devices[args[1]]
		if !ok {
			return fmt.Errorf("no device %q", args[1])
		}
		return printJSON(dev)

	case "invoke":
		// Named "invoke", not "run" -- "run" is already taken at the
		// top level for the no-daemon "start a runner directly" escape
		// hatch (plan.md's `flyball run <rig-file>`), so a device-command
		// verb can't reuse it without colliding.
		if len(args) < 3 {
			return fmt.Errorf("usage: flyball invoke <device> <command> [KEY=VALUE ...|JSON]")
		}
		body, err := commandBody(args[3:])
		if err != nil {
			return err
		}
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		var out any
		path := "/api/devices/" + args[1] + "/commands/" + args[2]
		if err := t.Do("POST", path, bytes.NewReader(data), &out); err != nil {
			return err
		}
		return printJSON(out)

	case "sessions":
		// /api/history/sessions, not /api/sessions -- cli.py's
		// cmd_sessions called the latter, which 404s against the real
		// server (checked live: history.py's router prefix is
		// /api/history). Fixed here rather than preserved as a bug.
		var out any
		if err := t.Do("GET", "/api/history/sessions", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "export":
		// cli.py's cmd_export hit /api/sessions/{id}/documents expecting
		// Bluesky event-model documents -- checked live against the real
		// server: that route doesn't exist. The real endpoint
		// (export.py) is /api/history/sessions/{id}/export?format=...,
		// returning a table (csv/json/zip), not per-document lines --
		// export.py's own docstring confirms the Bluesky-doc shape isn't
		// what's served today. This is a pre-existing break in cli.py,
		// not something this port preserves; --format picks the real
		// server's own formats instead of a fictional one.
		format, rest, hasFormat := popValue(args[1:], "--format")
		if !hasFormat {
			format = "json"
		}
		outPath, rest, hasOut := popValue(rest, "--out")
		if len(rest) != 1 {
			return fmt.Errorf("usage: flyball export <session> [--format csv|json|zip] [--out PATH]")
		}
		path := "/api/history/sessions/" + rest[0] + "/export?format=" + format
		data, err := t.Raw("GET", path, nil)
		if err != nil {
			return err
		}
		if hasOut {
			if err := os.WriteFile(outPath, data, 0o644); err != nil {
				return err
			}
			fmt.Printf("wrote %d bytes to %s\n", len(data), outPath)
			return nil
		}
		os.Stdout.Write(data)
		return nil

	case "program":
		return runProgramCommand(t, args[1:])

	case "sim":
		return runSimCommand(t, args[1:])

	default:
		return fmt.Errorf("unknown command %q; see `flyball` with no arguments for usage", args[0])
	}
}

func runProgramCommand(t client.Target, args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: flyball program check|run|status|stop ...")
	}
	switch args[0] {
	case "check":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball program check <path>")
		}
		doc, err := readJSONOrYAMLFile(args[1])
		if err != nil {
			return err
		}
		var out any
		if err := t.Do("POST", "/api/programs/check", jsonReader(doc), &out); err != nil {
			return err
		}
		return printJSON(out)
	case "run":
		interrupt, rest := popBool(args[1:], "--interrupt")
		if len(rest) != 1 {
			return fmt.Errorf("usage: flyball program run <path> [--interrupt]")
		}
		doc, err := readJSONOrYAMLFile(rest[0])
		if err != nil {
			return err
		}
		path := "/api/programs/run"
		if interrupt {
			path += "?interrupt=true"
		}
		var out any
		if err := t.Do("POST", path, jsonReader(doc), &out); err != nil {
			return err
		}
		return printJSON(out)
	case "status":
		var out any
		if err := t.Do("GET", "/api/programs/running", nil, &out); err != nil {
			return err
		}
		return printJSON(out)
	case "stop":
		var out any
		if err := t.Do("POST", "/api/programs/interrupt", nil, &out); err != nil {
			return err
		}
		return printJSON(out)
	default:
		return fmt.Errorf("unknown program command %q", args[0])
	}
}

func runSimCommand(t client.Target, args []string) error {
	action := "show"
	if len(args) > 0 {
		action = args[0]
		args = args[1:]
	}
	switch action {
	case "show":
		var out any
		if err := t.Do("GET", "/api/sim", nil, &out); err != nil {
			return err
		}
		return printJSON(out)
	case "clock":
		if len(args) != 1 {
			return fmt.Errorf("usage: flyball sim clock <speed>")
		}
		body, _ := json.Marshal(map[string]any{"speed": jsonOrString(args[0])})
		var out any
		if err := t.Do("PUT", "/api/sim/clock", bytes.NewReader(body), &out); err != nil {
			return err
		}
		return printJSON(out)
	case "step":
		if len(args) != 1 {
			return fmt.Errorf("usage: flyball sim step <seconds>")
		}
		body, _ := json.Marshal(map[string]any{"seconds": jsonOrString(args[0])})
		var out any
		if err := t.Do("POST", "/api/sim/clock/step", bytes.NewReader(body), &out); err != nil {
			return err
		}
		return printJSON(out)
	case "set":
		if len(args) < 2 {
			return fmt.Errorf("usage: flyball sim set <plant> KEY=VALUE ...")
		}
		params := map[string]any{}
		for _, item := range args[1:] {
			k, v, ok := strings.Cut(item, "=")
			if !ok {
				return fmt.Errorf("%q: write KEY=VALUE", item)
			}
			params[k] = jsonOrString(v)
		}
		body, _ := json.Marshal(params)
		var out any
		if err := t.Do("PUT", "/api/sim/plants/"+args[0], bytes.NewReader(body), &out); err != nil {
			return err
		}
		return printJSON(out)
	case "reset":
		if len(args) < 2 {
			return fmt.Errorf("usage: flyball sim reset <plant> <output> [--input VALUE]")
		}
		inputStr, rest, hasInput := popValue(args, "--input")
		if len(rest) != 2 {
			return fmt.Errorf("usage: flyball sim reset <plant> <output> [--input VALUE]")
		}
		body := map[string]any{"output": jsonOrString(rest[1])}
		if hasInput {
			body["input"] = jsonOrString(inputStr)
		}
		data, _ := json.Marshal(body)
		var out any
		if err := t.Do("POST", "/api/sim/plants/"+rest[0]+"/reset", bytes.NewReader(data), &out); err != nil {
			return err
		}
		return printJSON(out)
	case "config":
		var out any
		if err := t.Do("GET", "/api/sim/config", nil, &out); err != nil {
			return err
		}
		return printJSON(out)
	case "save":
		body := map[string]any{}
		if len(args) == 1 {
			body["path"] = args[0]
		}
		data, _ := json.Marshal(body)
		var out map[string]any
		if err := t.Do("POST", "/api/sim/save", bytes.NewReader(data), &out); err != nil {
			return err
		}
		fmt.Printf("saved %v\n", out["path"])
		return nil
	default:
		return fmt.Errorf("unknown sim command %q", action)
	}
}

// commandBody builds a device command's JSON body from trailing args:
// either a single raw JSON object (`flyball invoke pumps set_fraction
// '{"wet_fraction":0.25}'`) or KEY=VALUE pairs (`flyball invoke pumps
// set_fraction wet_fraction=0.25`). No dotted-flag nesting or positional
// single-argument shorthand, unlike cli.py's schema-driven argparse --
// see main.go's doc comment on that deliberate simplification.
func commandBody(args []string) (map[string]any, error) {
	body := map[string]any{}
	if len(args) == 0 {
		return body, nil
	}
	if len(args) == 1 && strings.HasPrefix(strings.TrimSpace(args[0]), "{") {
		if err := json.Unmarshal([]byte(args[0]), &body); err != nil {
			return nil, fmt.Errorf("invalid JSON body: %w", err)
		}
		return body, nil
	}
	for _, item := range args {
		k, v, ok := strings.Cut(item, "=")
		if !ok {
			return nil, fmt.Errorf("%q: write KEY=VALUE, or a single JSON object", item)
		}
		body[k] = jsonOrString(v)
	}
	return body, nil
}

// jsonOrString mirrors cli.py's _json_or_str: a flag value is JSON if it
// parses, else the literal text -- "4" is 4, "on" is "on".
func jsonOrString(text string) any {
	var v any
	if err := json.Unmarshal([]byte(text), &v); err == nil {
		return v
	}
	return text
}

func watchStream(t client.Target, stream string) error {
	valid := map[string]bool{"samples": true, "controllers": true, "writes": true, "signals": true}
	if !valid[stream] {
		return fmt.Errorf("unknown stream %q; want one of samples, controllers, writes, signals", stream)
	}
	wsURL := strings.Replace(strings.TrimRight(t.BaseURL, "/"), "http://", "ws://", 1)
	wsURL = strings.Replace(wsURL, "https://", "wss://", 1)
	wsURL += t.Prefix + "/ws/" + stream

	conn, err := wsclient.Dial(wsURL, http.Header{})
	if err != nil {
		return fmt.Errorf("connecting to %s: %w", wsURL, err)
	}
	defer conn.Close()

	for {
		msg, err := conn.ReadMessage()
		if err != nil {
			return err
		}
		fmt.Println(string(msg))
	}
}

func printJSON(v any) error {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func jsonReader(v any) *bytes.Reader {
	data, _ := json.Marshal(v)
	return bytes.NewReader(data)
}

func readJSONOrYAMLFile(path string) (any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var v any
	if err := json.Unmarshal(data, &v); err != nil {
		return nil, fmt.Errorf("%s: not valid JSON (YAML program files aren't supported by this CLI yet): %w", path, err)
	}
	return v, nil
}

// printStatus is a plain-text rendering of GET /api/health plus
// devices/controllers/waits, mirroring cli.py's cmd_status layout
// closely enough to be recognisable, not byte-identical.
func printStatus(t client.Target, health any) error {
	h, _ := health.(map[string]any)
	ok, _ := h["ok"].(bool)
	uptime, _ := h["uptime_s"].(float64)
	recording, _ := h["recording"].(bool)
	word := "OK"
	if !ok {
		word = "ATTENTION"
	}
	rec := "no"
	if recording {
		rec = "yes"
	}
	fmt.Printf("%s  up %.0f s  recording=%s\n", word, uptime, rec)

	var devices []map[string]any
	if err := t.Do("GET", "/api/devices", nil, &devices); err == nil {
		for _, d := range devices {
			name, _ := d["name"].(string)
			driver, _ := d["driver"].(string)
			fmt.Printf("  device      %-16s %-20s\n", name, driver)
		}
	}
	var controllers []map[string]any
	if err := t.Do("GET", "/api/controllers", nil, &controllers); err == nil {
		for _, c := range controllers {
			name, _ := c["name"].(string)
			mode, _ := c["mode"].(string)
			fmt.Printf("  controller %-16s %-11s\n", name, mode)
		}
	}
	var waits map[string]any
	if err := t.Do("GET", "/api/waits", nil, &waits); err == nil {
		for name, w := range waits {
			wm, _ := w.(map[string]any)
			outcome, _ := wm["outcome"].(string)
			fmt.Printf("  waiting  %-16s %s\n", name, outcome)
		}
	}
	return nil
}
