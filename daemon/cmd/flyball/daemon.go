// Daemon-management commands (flyball daemon ...) and `flyball logs` --
// both talk to flyballd's own HTTP API (interface.md's table) directly,
// via FLYBALLD_URL, never routed through -s/a runner. Sketched but
// undecided in interface.md ("flyball daemon runners, flyball daemon
// start <manifest> are plausible shapes, not decided") -- this is that
// decision made concrete.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"

	"flyballd/internal/client"
)

func daemonURL() string {
	if u := os.Getenv("FLYBALLD_URL"); u != "" {
		return strings.TrimRight(u, "/")
	}
	return strings.TrimRight(client.DefaultDaemonURL, "/")
}

func runDaemonCommand(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: flyball daemon runners|start|stop|restart ...")
	}
	base := daemonURL()
	switch args[0] {
	case "runners":
		var out any
		if err := doJSON("GET", base+"/api/runners", nil, &out); err != nil {
			return err
		}
		return printJSON(out)

	case "start":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball daemon start <manifest.json>")
		}
		data, err := os.ReadFile(args[1])
		if err != nil {
			return err
		}
		var out any
		if err := doJSON("POST", base+"/api/runners", bytes.NewReader(data), &out); err != nil {
			return err
		}
		return printJSON(out)

	case "stop":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball daemon stop <name>")
		}
		return doJSON("DELETE", base+"/api/runners/"+args[1], nil, nil)

	case "restart":
		if len(args) != 2 {
			return fmt.Errorf("usage: flyball daemon restart <name>")
		}
		return doJSON("POST", base+"/api/runners/"+args[1]+"/restart", nil, nil)

	default:
		return fmt.Errorf("unknown daemon command %q", args[0])
	}
}

func runLogsCommand(args []string) error {
	if len(args) != 1 {
		return fmt.Errorf("usage: flyball logs <name>")
	}
	base := daemonURL()
	resp, err := http.Get(base + "/api/runners/" + args[0] + "/logs")
	if err != nil {
		return fmt.Errorf("reaching daemon at %s: %w", base, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		data, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("GET /api/runners/%s/logs: %s: %s", args[0], resp.Status, string(data))
	}
	_, err = io.Copy(os.Stdout, resp.Body)
	return err
}

func doJSON(method, url string, body io.Reader, out any) error {
	req, err := http.NewRequest(method, url, body)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		data, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("%s %s: %s: %s", method, url, resp.Status, string(data))
	}
	if out == nil {
		return nil
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if len(data) == 0 {
		return nil
	}
	return json.Unmarshal(data, out)
}
