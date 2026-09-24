// `flyball stop`: POST <root>/api/rig/stop through the front for a rig
// addressed by name (-s NAME, NAME, or FLYBALL_URL/FLYBALLD_URL), or
// SIGUSR1 straight to the runner for one named locally (--front-dir DIR,
// a RIG-FILE, --pid N: D-042) -- the runner's break-glass (§WP0-9,
// `install_break_glass` next to `_terminate_as_interrupt`). A signal needs
// no front, no credential and no network, the OS's own signal permission
// (same user, or root) being the only check (merge requirement 25), and
// cannot reach another rig that happens to answer at FLYBALL_URL.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"flyballd/internal/client"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/frontwire"
)

// stopTimeout bounds each HTTP call a stop makes (resolving the target,
// POST /api/rig/stop, flyballd's rig list): connect, request and the whole
// response. A front that accepts and never answers counts as unreachable
// -- the stop falls through to the signal, or says how to send one -- so
// nothing on the network can block a stop.
var stopTimeout = 5 * time.Second

// stopClient is the HTTP client for every call a stop makes. It follows
// no redirect: a 3xx (an SSO proxy sending the CLI to its sign-in page)
// would turn the POST into a GET and the sign-in page's 200 into a
// "success", so the 3xx itself is the answer, and is refused.
var stopClient = &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
	return http.ErrUseLastResponse
}}

// redirected is the refusal for a 3xx answer, naming where it pointed.
func redirected(resp *http.Response) error {
	return fmt.Errorf("%s, redirecting to %q (not followed): whatever answers there wants a sign-in the CLI cannot give -- pass --token, or address the front directly", resp.Status, resp.Header.Get("Location"))
}

func runStopCommand(server, token string, args []string) error {
	all, args := popBool(args, "--all")
	pidFlag, args, hasPID := popValue(args, "--pid")
	frontDir, args, hasFrontDir := popValue(args, "--front-dir")
	reason, args, hasReason := popValue(args, "--reason")
	if all {
		if len(args) > 0 || hasPID || hasFrontDir {
			return fmt.Errorf("usage: flyball stop --all [--reason TEXT] (every rig flyballd lists; no NAME, --pid or --front-dir)")
		}
		return stopAllRigs(token, reason)
	}
	if len(args) > 1 {
		return fmt.Errorf("usage: flyball stop [NAME | RIG-FILE] [--pid N] [--front-dir DIR] [--reason TEXT] | flyball stop --all [--reason TEXT]")
	}
	var name string
	if len(args) == 1 {
		name = args[0]
	}
	// --front-dir names a runner on this host; -s NAME, a NAME or a
	// RIG-FILE name one too, another way: both at once is ambiguous.
	if hasFrontDir && (server != "" || name != "") {
		return fmt.Errorf("usage: flyball stop --front-dir DIR takes no -s NAME, NAME or RIG-FILE: it signals the runner holding DIR/runner.lock")
	}

	if hasPID {
		pid, err := strconv.Atoi(pidFlag)
		if err != nil {
			return fmt.Errorf("--pid %q: %w", pidFlag, err)
		}
		noReason(hasReason)
		return signalStop(pid, "the runner's log")
	}

	// D-042: a runner named locally -- by its front-dir, or by the rig
	// file `flyball run` was started with -- is stopped by a signal alone.
	// No HTTP call: whatever answers at FLYBALL_URL may be another rig (a
	// stop there would stop the wrong one), or a 404, a 502 or the D-028
	// refusal 503, none of which would stop this one.
	if hasFrontDir {
		proc, p, err := lockHolder(filepath.Join(frontDir, frontdir.Lock))
		if err != nil {
			return err
		}
		noReason(hasReason)
		return signalProcess(proc, p, "the runner's log (for `flyball run`, the run.log its banner named; under flyballd, `flyball logs NAME`)")
	}
	if name != "" && isRigFileArg(name) {
		return stopRigFile(name, hasReason)
	}

	// -s NAME, a NAME, or nothing: the rig stop over HTTP, its report
	// printed. Only a 200 with a stop report is success (postStop).
	addressed := server
	if name != "" {
		addressed = name
	}
	ctx, cancel := context.WithTimeout(context.Background(), stopTimeout)
	defer cancel()
	target, err := resolveTargetContext(ctx, addressed)
	if err == nil {
		var refused bool
		refused, err = postStop(ctx, target.WithToken(token), reason)
		if err == nil || refused {
			return err // the report printed, or the front's refusal
		}
	}
	return fmt.Errorf("the front could not be reached (%v); on the rig's host, `flyball stop --front-dir DIR` (its runner.lock names the pid), `flyball stop RIG-FILE` or `flyball stop --pid N` signals the runner directly", err)
}

// isRigFileArg: a stop argument that names a rig file rather than a rig --
// one with a path separator, or a .yaml/.yml ending (D-042).
func isRigFileArg(arg string) bool {
	if strings.ContainsRune(arg, '/') || strings.ContainsRune(arg, filepath.Separator) {
		return true
	}
	ext := strings.ToLower(filepath.Ext(arg))
	return ext == ".yaml" || ext == ".yml"
}

// stopRigFile signals the runner `flyball run RIG-FILE` started: the one
// holding runner.lock in the front-dir derived from the rig file
// (frontwire.RunFrontDir, the rule `flyball run` itself uses).
func stopRigFile(rig string, hasReason bool) error {
	fi, err := os.Stat(rig)
	if err != nil {
		return fmt.Errorf("rig file %s: %w", rig, err)
	}
	if fi.IsDir() {
		return fmt.Errorf("rig file %s is a directory (for a front-dir, pass --front-dir DIR)", rig)
	}
	dir, ok := frontwire.RunFrontDir(rig)
	if !ok {
		return fmt.Errorf("the front-dir of %s cannot be derived here (no private XDG_RUNTIME_DIR): pass --front-dir DIR, as its `flyball run` banner printed, or --pid N", rig)
	}
	proc, p, err := lockHolder(filepath.Join(dir, frontdir.Lock))
	if errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("no `flyball run %s` is running on this host as this user (%s does not exist)", rig, filepath.Join(dir, frontdir.Lock))
	}
	if err != nil {
		return err
	}
	noReason(hasReason)
	where := "the runner's log"
	if id, err := frontdir.FrontID(rig); err == nil {
		if state, err := frontwire.RunDir(id); err == nil {
			where = filepath.Join(state, "run.log")
		}
	}
	return signalProcess(proc, p, where)
}

// noReason says --reason goes nowhere on a signal.
func noReason(given bool) {
	if given {
		fmt.Fprintln(os.Stderr, "flyball: --reason is not carried by a signal; the runner records the stop as local:signal")
	}
}

// stopAllRigs is `flyball stop --all` (D-037): the rig stop -- POST
// <root>/api/rig/stop, as `flyball stop NAME` -- on every rig flyballd
// (FLYBALLD_URL) lists for this credential (GET /api/rigs: the rigs it
// holds any verb on; no management scope needed), each report printed
// under the rig's name. The runner processes stay up. Any rig whose stop
// was refused or failed makes it an error naming them; so does a list
// with no rig in it, since nothing was stopped. No signal fallback: with
// flyballd unreachable there is no list, and each runner is stopped with
// `flyball stop --pid N` / `--front-dir DIR`.
func stopAllRigs(token, reason string) error {
	base := daemonURL()
	daemon := client.Target{BaseURL: base}.WithToken(token)
	ctx, cancel := context.WithTimeout(context.Background(), stopTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", base+"/api/rigs", nil)
	if err != nil {
		return err
	}
	for k, vs := range daemon.AuthHeaders() {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	resp, err := stopClient.Do(req)
	if err != nil {
		return fmt.Errorf("flyballd at %s cannot be reached (%v): nothing was stopped; stop each runner with `flyball stop --pid N` or `--front-dir DIR`", base, err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		return fmt.Errorf("listing the rigs at %s: %v; nothing was stopped", base, redirected(resp))
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("listing the rigs at %s: %s: %s; nothing was stopped", base, resp.Status, strings.TrimSpace(string(body)))
	}
	var rigs []struct {
		Name     string `json:"name"`
		RootPath string `json:"root_path"`
	}
	if err := json.Unmarshal(body, &rigs); err != nil {
		return fmt.Errorf("listing the rigs at %s: %v; nothing was stopped", base, err)
	}
	if len(rigs) == 0 {
		return fmt.Errorf("flyballd at %s lists no rig this credential holds a verb on (pass --token, or set FLYBALL_TOKEN); nothing was stopped", base)
	}
	var failed []string
	for _, rig := range rigs {
		fmt.Printf("%s:\n", rig.Name)
		target := client.Target{BaseURL: base, Prefix: rig.RootPath}.WithToken(token)
		ctx, cancel := context.WithTimeout(context.Background(), stopTimeout)
		_, err := postStop(ctx, target, reason)
		cancel()
		if err != nil {
			fmt.Printf("  software stop refused or failed: %v\n", err)
			failed = append(failed, rig.Name)
		}
	}
	if len(failed) > 0 {
		return fmt.Errorf("%d of %d rigs refused or failed the software stop: %s", len(failed), len(rigs), strings.Join(failed, ", "))
	}
	return nil
}

// postStop sends the stop request. refused is true when the front
// answered with anything but a stop report (a real refusal, e.g. missing
// OPERATE, any other >=400, a redirect, or a 200 that is not a report):
// the caller should report that error, not fall back to a signal. A nil
// error with refused false is success: a 200 whose body is a stop report,
// printed already. ctx bounds the whole exchange (stopTimeout).
func postStop(ctx context.Context, t client.Target, reason string) (refused bool, err error) {
	body := map[string]string{}
	if reason != "" {
		body["reason"] = reason
	}
	data, _ := json.Marshal(body)
	url := strings.TrimRight(t.BaseURL, "/") + t.Prefix + "/api/rig/stop"
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(data))
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
	resp, err := stopClient.Do(req)
	if err != nil {
		return false, err // network-level: not a refusal, the caller falls back
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(resp.Body)
	if err != nil {
		return false, err
	}
	switch {
	case resp.StatusCode >= 300 && resp.StatusCode < 400:
		return true, fmt.Errorf("stop: %v; nothing was stopped", redirected(resp))
	case resp.StatusCode != http.StatusOK:
		return true, fmt.Errorf("stop: %s: %s", resp.Status, strings.TrimSpace(string(out)))
	}
	var r stopReport
	if err := json.Unmarshal(out, &r); err != nil || r.AtNS == 0 {
		return true, fmt.Errorf("stop: %s, but the answer is not a stop report, so the stop is not known to have happened: %.200s", resp.Status, strings.TrimSpace(string(out)))
	}
	printStopReport(r)
	return false, nil
}

// stopReport mirrors A8's StopReport JSON (§WP0-9, "A8 as built"):
// {at_ns, actor{sub,sid,kind,via,detail}, reason, devices{name:
// {state,detail,written,kept}}, program_interrupted, controllers_manual, interim,
// latched}.
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
	Latched            bool                       `json:"latched"`
}

type stopDeviceState struct {
	State  string `json:"state"`
	Detail string `json:"detail"`
}

// printStopReport renders r to stdout.
func printStopReport(r stopReport) {
	fmt.Printf("software stop: %s by %s (%s) via %s\n", r.Reason, r.Actor.Sub, r.Actor.Kind, r.Actor.Via)
	if r.Interim {
		fmt.Println("  controllers to manual; nothing written -- outputs left as they were")
	}
	if r.ProgramInterrupted {
		fmt.Println("  program interrupted")
	}
	if r.Latched {
		fmt.Println("  latched: automatic writes are refused until a person resets it (Reset in the UI, or POST /api/rig/reset)")
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
func signalStop(pid int, where string) error {
	proc, err := os.FindProcess(pid)
	if err != nil {
		return fmt.Errorf("pid %d: %w", pid, err)
	}
	// Under `flyball run --uv` or flyballd's `uv_project:` the process
	// spawned is uv (the pid `flyball runners` shows), with the runner as
	// its child. uv does not pass SIGUSR1 on: it would die of it, leave
	// the runner orphaned, and stop nothing.
	if processName(pid) == "uv" {
		if child := childOf(pid); child != 0 {
			return fmt.Errorf("pid %d is uv, which does not pass SIGUSR1 on (it would die of it, and the rig would not stop): nothing was signalled; the runner under it is pid %d -- `flyball stop --pid %d`", pid, child, child)
		}
		return fmt.Errorf("pid %d is uv, which does not pass SIGUSR1 on (it would die of it, and the rig would not stop): nothing was signalled; signal the runner under it -- `flyball stop --front-dir DIR` or `flyball stop RIG-FILE` find it by its runner.lock", pid)
	}
	return signalProcess(proc, pid, where)
}

// signalProcess sends SIGUSR1 and says where the stop report goes: the
// signal carries no answer back.
func signalProcess(proc *os.Process, pid int, where string) error {
	if err := proc.Signal(syscall.SIGUSR1); err != nil {
		return fmt.Errorf("signalling pid %d: %w", pid, err)
	}
	fmt.Printf("sent SIGUSR1 to pid %d; the stop report goes to %s\n", pid, where)
	return nil
}

// lockPidRe matches the first line of both lock file shapes
// (frontdir.Lock's "pid <n> rig <name>" and the bare runner's own
// "<store>.lock"'s "pid <n>: <argv...>", engine/src/flyball/runner/
// locking.py's hold_front/hold): both start "pid <n>".
var lockPidRe = regexp.MustCompile(`^pid (\d+)`)

// lockHolder is the runner that holds the lock file at path (runner.lock,
// or a bare runner's <store>.lock: the runner holds LOCK_EX on either for
// its life, and writes its pid into it once it holds it), for
// signalStop's fallback. The file is never unlinked, so the pid in it
// outlives its runner and may since have been given to an unrelated
// process. The pid is signalled only when the lock is held now (a
// LOCK_SH|LOCK_NB probe must fail, the rule flyballd's own adoption uses,
// frontdir.LockHeld) and that pid is known to be its holder: where
// /proc/locks exists (Linux), it shows that pid holding it; elsewhere,
// the process started no later than the file was last written, so it is
// the runner that wrote it (processStartTime; macOS). Where neither can
// be known, nothing is signalled. A held lock that names no pid is a
// runner starting: a front's Write empties runner.lock while it holds it
// (frontdir), and the runner writes its pid right after it takes it. The
// *os.Process is taken before those checks -- a pidfd on Linux -- so the
// signal cannot reach a later process given the same pid in between.
func lockHolder(path string) (*os.Process, int, error) {
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, 0, fmt.Errorf("reading %s: %w", path, err)
	}
	defer f.Close()
	data, err := io.ReadAll(f)
	if err != nil {
		return nil, 0, fmt.Errorf("reading %s: %w", path, err)
	}
	fi, err := f.Stat()
	if err != nil {
		return nil, 0, fmt.Errorf("reading %s: %w", path, err)
	}
	pid := 0
	if m := lockPidRe.FindSubmatch(data); m != nil {
		pid, err = strconv.Atoi(string(m[1]))
		if err != nil || pid <= 0 {
			return nil, 0, fmt.Errorf("%s: bad pid %q", path, m[1])
		}
	}
	var proc *os.Process
	if pid > 0 {
		if proc, err = os.FindProcess(pid); err != nil {
			return nil, 0, fmt.Errorf("pid %d: %w", pid, err)
		}
	}
	switch err := syscall.Flock(int(f.Fd()), syscall.LOCK_SH|syscall.LOCK_NB); {
	case err == nil:
		syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
		if pid == 0 {
			return nil, 0, fmt.Errorf("%s is stale: no runner holds it", path)
		}
		return nil, 0, fmt.Errorf("%s is stale: no runner holds it (the one that wrote it has exited), so pid %d, which it names, is not signalled", path, pid)
	case !errors.Is(err, syscall.EWOULDBLOCK):
		return nil, 0, fmt.Errorf("%s: %w", path, err)
	}
	if pid == 0 {
		return nil, 0, fmt.Errorf("the runner is starting (%s is held but names no pid yet): nothing was signalled; try again in a moment", path)
	}
	holders, known := flockHolders(f)
	if known && !slices.Contains(holders, pid) {
		return nil, 0, fmt.Errorf("%s names pid %d, but its lock is held by pid %v: a runner starting (it names itself in a moment), or not a runner at all; pid %d is not signalled -- try again in a moment, or pass --pid N once you know which is the runner", path, pid, holders, pid)
	}
	if !known {
		started, ok := processStartTime(pid)
		switch {
		case !ok:
			return nil, 0, fmt.Errorf("%s names pid %d, but this system cannot confirm that pid %d holds it (no /proc/locks), and the pid may since be another process's: nothing was signalled; check pid %d is the runner (`ps -p %d`), then `flyball stop --pid %d` -- or press Ctrl-C in the `flyball run` terminal", path, pid, pid, pid, pid, pid)
		case started.After(fi.ModTime()):
			return nil, 0, fmt.Errorf("%s names pid %d, but pid %d started after the file was written, so it is not the runner that wrote it (the runner is starting, or has exited): nothing was signalled; try again in a moment, or `flyball stop --pid N` for the runner's own pid", path, pid, pid)
		}
	}
	return proc, pid, nil
}

// flockHolders is the pids /proc/locks shows holding a flock on f's
// file; known is false where /proc/locks cannot be read.
func flockHolders(f *os.File) (pids []int, known bool) {
	fi, err := f.Stat()
	if err != nil {
		return nil, false
	}
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return nil, false
	}
	locks, err := os.ReadFile(procLocks)
	if err != nil {
		return nil, false
	}
	// /proc/locks prints the device as MAJOR:MINOR in hex, from the
	// kernel's dev_t; st_dev is its userspace encoding.
	dev := uint64(st.Dev)
	major := (dev>>8)&0xfff | (dev>>32)&^uint64(0xfff)
	minor := dev&0xff | (dev>>12)&^uint64(0xff)
	id := fmt.Sprintf("%02x:%02x:%d", major, minor, st.Ino)
	for _, line := range strings.Split(string(locks), "\n") {
		// "1: FLOCK  ADVISORY  WRITE 1234 fd:01:5678 0 EOF"; a waiter's
		// line has "->" after the number and is skipped.
		fl := strings.Fields(line)
		if len(fl) < 6 || fl[1] != "FLOCK" || fl[5] != id {
			continue
		}
		if n, err := strconv.Atoi(fl[4]); err == nil {
			pids = append(pids, n)
		}
	}
	return pids, true
}

// procLocks is /proc/locks; a test points it elsewhere to play a system
// without it (macOS).
var procLocks = "/proc/locks"

// pidFromLockFile is the pid a lock file names, unverified: for naming it
// in a message (run.go's refusal). Never signal it -- lockHolder is the
// checked form.
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
