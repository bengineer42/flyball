// `flyball stop`: POST <root>/api/rig/stop through the front, or, when
// the front can't be reached, SIGUSR1 straight to the runner (§WP0-9's
// break-glass, installed by `install_break_glass` next to
// `_terminate_as_interrupt`) -- a stop must work even with no front, no
// credential and no network, the OS's own signal permission (same user,
// or root) being the only check (merge requirement 25).
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"

	"flyballd/internal/client"
)

func runStopCommand(server, token string, args []string) error {
	pidFlag, args, hasPID := popValue(args, "--pid")
	frontDir, args, hasFrontDir := popValue(args, "--front-dir")
	reason, args, _ := popValue(args, "--reason")
	if len(args) > 1 {
		return fmt.Errorf("usage: flyball stop [NAME] [--pid N] [--front-dir DIR] [--reason TEXT]")
	}
	var name string
	if len(args) == 1 {
		name = args[0]
	}

	var pid int
	if hasPID {
		n, err := strconv.Atoi(pidFlag)
		if err != nil {
			return fmt.Errorf("--pid %q: %w", pidFlag, err)
		}
		pid = n
	}

	// --pid is the direct escape hatch (a bare runner has no front at
	// all to try first, and no runner.lock either): skip the HTTP route
	// and signal straight away.
	if pid == 0 {
		addressed := server
		if name != "" {
			addressed = name
		}
		target, err := resolveTarget(addressed)
		if err == nil {
			target = target.WithToken(token)
			refused, postErr := postStop(target, reason)
			switch {
			case postErr == nil:
				return nil
			case refused:
				return postErr
			}
			// A network-level failure (dial/timeout/refused): fall
			// through to the signal fallback below rather than giving up.
		}
	}

	switch {
	case pid != 0:
		return signalStop(pid)
	case hasFrontDir:
		p, err := pidFromLockFile(frontDir + "/runner.lock")
		if err != nil {
			return err
		}
		return signalStop(p)
	default:
		return fmt.Errorf("the front could not be reached; pass --front-dir DIR (its runner.lock names the pid) or --pid N to stop the runner directly")
	}
}

// postStop sends the stop request. refused is true when the front
// answered (a real refusal, e.g. missing OPERATE, or any other >=400):
// the caller should report that error, not fall back to a signal. A nil
// error with refused false is success (the report was printed already).
func postStop(t client.Target, reason string) (refused bool, err error) {
	body := map[string]string{}
	if reason != "" {
		body["reason"] = reason
	}
	data, _ := json.Marshal(body)
	url := strings.TrimRight(t.BaseURL, "/") + t.Prefix + "/api/rig/stop"
	req, err := http.NewRequest("POST", url, bytes.NewReader(data))
	if err != nil {
		return false, err
	}
	req.Header.Set("Content-Type", "application/json")
	// A stop acts, so the front's Origin check applies to every scheme but
	// a bearer token (merge requirement 9, front/proxy.go). A tokenless
	// CLI call carries no browser Origin at all, so it must set one
	// itself, matching the front's own base origin (B1's "C2" note).
	req.Header.Set("Origin", strings.TrimRight(t.BaseURL, "/"))
	for k, vs := range t.AuthHeaders() {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return false, err // network-level: not a refusal, the caller falls back
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(resp.Body)
	if err != nil {
		return false, err
	}
	if resp.StatusCode >= 400 {
		return true, fmt.Errorf("stop: %s: %s", resp.Status, strings.TrimSpace(string(out)))
	}
	printStopReport(out)
	return false, nil
}

// stopReport mirrors A8's StopReport JSON (§WP0-9, "A8 as built"):
// {at_ns, actor{sub,sid,kind,via,detail}, reason, devices{name:
// {state,detail}}, program_interrupted, controllers_manual, interim}.
type stopReport struct {
	AtNS  int64 `json:"at_ns"`
	Actor struct {
		Sub    string `json:"sub"`
		Sid    string `json:"sid"`
		Kind   string `json:"kind"`
		Via    string `json:"via"`
		Detail string `json:"detail"`
	} `json:"actor"`
	Reason             string                     `json:"reason"`
	Devices            map[string]stopDeviceState `json:"devices"`
	ProgramInterrupted bool                       `json:"program_interrupted"`
	ControllersManual  []string                   `json:"controllers_manual"`
	Interim            bool                       `json:"interim"`
}

type stopDeviceState struct {
	State  string `json:"state"`
	Detail string `json:"detail"`
}

// printStopReport renders r to stdout; an unrecognised shape is printed
// as raw JSON rather than silently dropped.
func printStopReport(raw []byte) {
	var r stopReport
	if err := json.Unmarshal(raw, &r); err != nil {
		fmt.Println(string(raw))
		return
	}
	fmt.Printf("stopped: %s by %s (%s) via %s\n", r.Reason, r.Actor.Sub, r.Actor.Kind, r.Actor.Via)
	if r.Interim {
		fmt.Println("  interim report (the signals work's Stopper has not landed yet)")
	}
	if r.ProgramInterrupted {
		fmt.Println("  program interrupted")
	}
	names := make([]string, 0, len(r.Devices))
	for name := range r.Devices {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		d := r.Devices[name]
		fmt.Printf("  device %-20s %-8s %s\n", name, d.State, d.Detail)
	}
	if len(r.ControllersManual) > 0 {
		controllers := append([]string(nil), r.ControllersManual...)
		sort.Strings(controllers)
		fmt.Println("  controllers set to manual:", strings.Join(controllers, ", "))
	}
}

// signalStop sends SIGUSR1 to pid, the runner's own break-glass stop
// (engine/src/flyball/runner/stopping.py's install_break_glass): it never
// exits, and its report goes to the runner's own log, not to this
// process, since nothing but the OS heard from us.
func signalStop(pid int) error {
	proc, err := os.FindProcess(pid)
	if err != nil {
		return fmt.Errorf("pid %d: %w", pid, err)
	}
	if err := proc.Signal(syscall.SIGUSR1); err != nil {
		return fmt.Errorf("signalling pid %d: %w", pid, err)
	}
	fmt.Printf("sent SIGUSR1 to pid %d; see the runner's log for the stop report\n", pid)
	return nil
}

// lockPidRe matches the first line of both lock file shapes
// (frontdir.Lock's "pid <n> rig <name>" and the bare runner's own
// "<store>.lock"'s "pid <n>: <argv...>", engine/src/flyball/runner/
// locking.py's hold_front/hold): both start "pid <n>".
var lockPidRe = regexp.MustCompile(`^pid (\d+)`)

func pidFromLockFile(path string) (int, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return 0, fmt.Errorf("reading %s: %w", path, err)
	}
	m := lockPidRe.FindSubmatch(data)
	if m == nil {
		return 0, fmt.Errorf(`%s: does not start with "pid <n>"`, path)
	}
	n, err := strconv.Atoi(string(m[1]))
	if err != nil {
		return 0, fmt.Errorf("%s: %w", path, err)
	}
	return n, nil
}
