// `flyball runners stop NAME|--all`: end runner processes through
// flyballd's management API (DELETE /api/runners/{name}) -- for
// maintenance or an uninstall. flyballd itself leaves its runners running
// when it stops, and adopts them when it starts again (D-037), so this is
// how they are ended. Not the rig stop: `flyball stop` interrupts a rig
// and leaves its runner up.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"

	"flyballd/internal/grants"
)

// runRunnersCommand runs `flyball runners ...`. token is the global
// --token, which wins over FLYBALLD_TOKEN: either must carry the manage
// scope (`flyball token create --scope manage`).
func runRunnersCommand(token string, args []string) error {
	const usage = "usage: flyball runners stop NAME | flyball runners stop --all"
	if len(args) == 0 || args[0] != "stop" {
		return fmt.Errorf("%s", usage)
	}
	all, rest := popBool(args[1:], "--all")
	if all == (len(rest) == 1) || len(rest) > 1 {
		return fmt.Errorf("%s", usage)
	}
	if token == "" {
		token = os.Getenv("FLYBALLD_TOKEN")
	}
	base := daemonURL()

	type runner struct {
		Name    string `json:"name"`
		Status  string `json:"status"`
		Pid     int    `json:"pid"`
		Adopted bool   `json:"adopted"`
	}
	var runners []runner
	if all {
		body, err := manageRequest("GET", base+"/api/runners", token)
		if err != nil {
			return fmt.Errorf("listing the runners: %w; nothing was stopped", err)
		}
		if err := json.Unmarshal(body, &runners); err != nil {
			return fmt.Errorf("listing the runners: %v; nothing was stopped", err)
		}
		if len(runners) == 0 {
			fmt.Println("flyballd has no runners registered")
			return nil
		}
	} else {
		runners = []runner{{Name: rest[0]}}
	}

	var failed []string
	for _, r := range runners {
		if _, err := manageRequest("DELETE", base+"/api/runners/"+r.Name, token); err != nil {
			fmt.Printf("%s: not stopped: %v\n", r.Name, err)
			failed = append(failed, r.Name)
			continue
		}
		switch {
		case r.Status == "busy":
			fmt.Printf("%s: stopped (deregistered; it was busy -- the process holding its front-dir is not flyballd's, and was not signalled)\n", r.Name)
		case r.Pid != 0:
			fmt.Printf("%s: stopped (pid %d)\n", r.Name, r.Pid)
		default:
			fmt.Printf("%s: stopped\n", r.Name)
		}
	}
	if len(failed) > 0 {
		return fmt.Errorf("%d of %d runners not stopped: %s", len(failed), len(runners), strings.Join(failed, ", "))
	}
	return nil
}

// manageRequest sends a management request with token as the bearer and
// returns the body of a 2xx answer; a 401/403 says the manage scope is
// needed.
func manageRequest(method, url, token string) ([]byte, error) {
	req, err := http.NewRequest(method, url, nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("flyballd cannot be reached: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	switch {
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden:
		return nil, fmt.Errorf("%s: %s: needs a token with the %s scope (--token T or FLYBALLD_TOKEN; `flyball token create --scope %s`)",
			resp.Status, strings.TrimSpace(string(body)), grants.Management(), grants.Management())
	case resp.StatusCode >= 300:
		return nil, fmt.Errorf("%s: %s", resp.Status, strings.TrimSpace(string(body)))
	}
	return body, nil
}
