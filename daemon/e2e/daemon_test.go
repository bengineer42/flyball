//go:build e2e

package e2e

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/principal"
)

// runnerRow is one runner in flyballd's GET /api/runners.
type runnerRow struct {
	Name     string `json:"name"`
	Status   string `json:"status"`
	Endpoint string `json:"endpoint"`
}

// createToken runs `flyball token create` offline against config and
// returns the secret.
func (e *env) createToken(config, name string, scopes ...string) string {
	e.t.Helper()
	args := []string{"token", "create", "--config", config, "--name", name}
	for _, s := range scopes {
		args = append(args, "--scope", s)
	}
	out, errOut, code := e.run(nil, "flyball", args...)
	out = strings.TrimSpace(out)
	if code != 0 || !strings.HasPrefix(out, "fbt1_") {
		e.t.Fatalf("flyball token create %s: %d %q %s", name, code, out, errOut)
	}
	return out
}

// runners lists flyballd's runners with a management token.
func runners(t *testing.T, base, tok string) map[string]runnerRow {
	t.Helper()
	r := do(t, hc, "GET", base+"/api/runners", "", bearer(tok))
	if r.Status != 200 {
		t.Fatalf("GET /api/runners: %v", r)
	}
	var rows []runnerRow
	r.json(t, &rows)
	out := map[string]runnerRow{}
	for _, x := range rows {
		out[x.Name] = x
	}
	return out
}

// flyballd with two rigs behind its front (password shape): management
// needs a bearer token with the manage scope, never a session; a scope for
// rig a does not reach rig b; a principal for a is refused at b; kill -9
// on a's runner brings a respawn with a fresh key; the runner's own
// execv restart keeps working through the front; a second flyballd over
// the same manifests leaves the first's runners and keys alone.
func TestDaemonTwoRigs(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	for _, name := range []string{"a", "b"} {
		rig := e.rig(name, "runner:\n  allow_shutdown: true\n")
		e.write("manifests/"+name+".yaml", fmt.Sprintf("name: %s\nserver_config: %s\nroot_path: /%s\nrestart: always\n", name, rig, name))
	}
	// c's root lies under a's: refused at registration (F6).
	e.write("manifests/c.yaml", fmt.Sprintf("name: c\nserver_config: %s\nroot_path: /a/c\n", e.rig("c", "")))
	cfg := e.write("flyballd.yaml", fmt.Sprintf("listen: 127.0.0.1:0\nauth: password\npassword: '%s'\nmanifests_dir: manifests\ndata_dir: data\n", passwordLine))
	mgmt := e.createToken(cfg, "mgmt", "manage")
	readA := e.createToken(cfg, "reader-a", "read:a")
	readB := e.createToken(cfg, "reader-b", "read:b")
	opA := e.createToken(cfg, "op-a", "operate:a", "read:a")

	d := e.start("flyballd", "flyballd", "--config", cfg)
	base := "http://" + d.waitOutput(`flyballd listening on (\S+) \(`, 60*time.Second)[1]
	waitStatus(t, hc, "GET", base+"/a/api/runner", bearer(readA), 200, 90*time.Second)
	waitStatus(t, hc, "GET", base+"/b/api/runner", bearer(readB), 200, 90*time.Second)
	storeA := e.path("a", "oven.sqlite")

	var dirA, dirB string
	t.Run("management needs the manage scope by bearer", func(t *testing.T) {
		if r := do(t, hc, "GET", base+"/api/runners", "", nil); r.Status != 401 {
			t.Errorf("no credential: %v, want 401", r)
		}
		if r := do(t, hc, "GET", base+"/api/runners", "", bearer(readA)); r.Status != 403 {
			t.Errorf("a rig token: %v, want 403", r)
		}
		ck := login(t, hc, base)
		if r := do(t, hc, "GET", base+"/api/runners", "", h{"Cookie", ck.Name + "=" + ck.Value}); r.Status != 403 {
			t.Errorf("the admin session: %v, want 403 (a sign-in never grants manage)", r)
		}
		if r := do(t, hc, "POST", base+"/api/runners/a/restart", "", cat(origin(base), h{"Cookie", ck.Name + "=" + ck.Value})); r.Status != 403 {
			t.Errorf("restart with the admin session: %v, want 403", r)
		}
		rs := runners(t, base, mgmt)
		for _, n := range []string{"a", "b"} {
			if rs[n].Status != "running" || !strings.HasPrefix(rs[n].Endpoint, "unix:") {
				t.Errorf("runner %s: %+v, want running on a unix socket", n, rs[n])
			}
		}
		if _, ok := rs["c"]; ok || !strings.Contains(d.output(), `failed to start runner "c"`) {
			t.Errorf("c, whose root /a/c overlaps a's: %+v, want refused at registration", rs["c"])
		}
		dirA = filepath.Dir(strings.TrimPrefix(rs["a"].Endpoint, "unix:"))
		dirB = filepath.Dir(strings.TrimPrefix(rs["b"].Endpoint, "unix:"))
		if readFile(t, filepath.Join(dirA, "aud")) != "a" || readFile(t, filepath.Join(dirB, "aud")) != "b" {
			t.Errorf("aud: a's %q, b's %q; want the manifest names", readFile(t, filepath.Join(dirA, "aud")), readFile(t, filepath.Join(dirB, "aud")))
		}
	})
	if dirA == "" {
		t.FailNow()
	}

	t.Run("keys in no argv, environment or log; runner logs 0600", func(t *testing.T) {
		for _, dir := range []string{dirA, dirB} {
			key := readFile(t, filepath.Join(dir, "key"))
			for _, pid := range []int{lockPid(t, dir), d.pid()} {
				for _, f := range []string{"cmdline", "environ"} {
					b, err := os.ReadFile(fmt.Sprintf("/proc/%d/%s", pid, f))
					if err != nil {
						t.Fatal(err)
					}
					if strings.Contains(string(b), key) {
						t.Errorf("a runner's key is in /proc/%d/%s", pid, f)
					}
				}
			}
			for _, log := range []string{d.log, e.path("data", "logs", "a.log"), e.path("data", "logs", "b.log")} {
				if strings.Contains(readFile(t, log), key) {
					t.Errorf("a runner's key is in %s", log)
				}
			}
		}
		for _, log := range []string{"a.log", "b.log"} {
			st, err := os.Stat(e.path("data", "logs", log))
			if err != nil || st.Mode().Perm() != 0o600 {
				t.Errorf("runner log %s: %v %v, want 0600", log, st.Mode(), err)
			}
		}
	})

	t.Run("a scope for rig a does not reach rig b", func(t *testing.T) {
		if info := authInfo(t, hc, base+"/a", bearer(readA)); verbs(info) != "read" || info.Rig != "a" {
			t.Errorf("reader-a at a: %v (rig %q)", info.Verbs, info.Rig)
		}
		if r := do(t, hc, "GET", base+"/api/auth", "", bearer(readA)); r.Status != 200 || strings.Contains(string(r.Body), `"rig"`) {
			t.Errorf("/api/auth at the daemon root: %v, want no rig", r)
		}
		if info := authInfo(t, hc, base+"/b", bearer(readA)); verbs(info) != "" {
			t.Errorf("reader-a at b: %v, want none", info.Verbs)
		}
		if r := do(t, hc, "GET", base+"/b/api/runner", "", bearer(readA)); r.Status != 403 {
			t.Errorf("reader-a reads b: %v, want 403", r)
		}
		if r := do(t, hc, "POST", base+"/b/api/rig/stop", `{"reason":"cross"}`, bearer(opA)); r.Status != 403 {
			t.Errorf("op-a stops b: %v, want 403", r)
		}
		if r := do(t, hc, "POST", base+"/a/api/rig/stop", `{"reason":"e2e-daemon"}`, bearer(opA)); r.Status != 200 {
			t.Errorf("op-a stops a: %v", r)
		}
		waitAudit(t, storeA, map[string]string{"route": "/api/rig/stop", "sub": "token:op-a", "details": "e2e-daemon"})
	})

	t.Run("a principal for a is refused at b", func(t *testing.T) {
		now := time.Now()
		sockB := unixClient(filepath.Join(dirB, "sock"))
		for _, c := range []struct{ name, tok, code string }{
			{"a's principal replayed at b", mint(t, dirA, "", []string{"operate", "read"}, now), "mac"},
			{"b's key, a's aud", mint(t, dirB, "a", []string{"operate", "read"}, now), "aud"},
		} {
			r := do(t, sockB, "GET", "http://localhost/b/api/runner", "", h{principal.Header, c.tok})
			if r.Status != 401 || r.Header.Get("X-Flyball-Principal-Error") != c.code {
				t.Errorf("%s: %v (%s), want 401 %s", c.name, r, r.Header.Get("X-Flyball-Principal-Error"), c.code)
			}
		}
		if r := do(t, unixClient(filepath.Join(dirA, "sock")), "GET", "http://localhost/a/api/runner", "",
			h{principal.Header, mint(t, dirA, "", []string{"read"}, now)}); r.Status != 200 {
			t.Fatalf("a's own principal at a: %v (the check cannot bite)", r)
		}
	})

	t.Run("flyball stop through flyballd", func(t *testing.T) {
		out, errOut, code := e.run([]string{"FLYBALLD_URL=" + base}, "flyball", "-s", "a", "--token", opA, "stop", "--reason", "e2e-daemon-cli")
		if code != 0 || !strings.Contains(out, "software stop: e2e-daemon-cli by token:op-a") {
			t.Fatalf("flyball -s a stop: %d\n%s%s", code, out, errOut)
		}
	})

	t.Run("kill -9 on a runner: respawn, fresh key, 200", func(t *testing.T) {
		pid, key := lockPid(t, dirA), readFile(t, filepath.Join(dirA, "key"))
		if err := syscall.Kill(pid, syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		for end := time.Now().Add(60 * time.Second); time.Now().Before(end); time.Sleep(200 * time.Millisecond) {
			b, _ := os.ReadFile(filepath.Join(dirA, "runner.lock"))
			if f := strings.Fields(string(b)); len(f) > 1 && f[1] != fmt.Sprint(pid) {
				break
			}
		}
		waitStatus(t, hc, "GET", base+"/a/api/runner", bearer(readA), 200, 60*time.Second)
		if lockPid(t, dirA) == pid {
			t.Fatal("no new runner for a")
		}
		if readFile(t, filepath.Join(dirA, "key")) == key {
			t.Error("the respawned runner has the old key")
		}
		if readFile(t, filepath.Join(dirA, "aud")) != "a" {
			t.Error("the aud changed across a respawn")
		}
		if r := do(t, hc, "GET", base+"/b/api/runner", "", bearer(readB)); r.Status != 200 {
			t.Errorf("b, untouched: %v", r)
		}
	})

	t.Run("POST /api/runner/restart (execv) through the front", func(t *testing.T) {
		logA := e.path("data", "logs", "a.log")
		starts := func() int {
			b, _ := os.ReadFile(logA)
			return len(regexp.MustCompile(`Uvicorn running on`).FindAll(b, -1))
		}
		before, pid := starts(), lockPid(t, dirA)
		if r := do(t, hc, "POST", base+"/a/api/runner/restart", "", bearer(opA)); r.Status != 202 {
			t.Fatalf("restart: %v", r)
		}
		for end := time.Now().Add(60 * time.Second); starts() <= before && time.Now().Before(end); time.Sleep(200 * time.Millisecond) {
		}
		if starts() <= before {
			t.Fatalf("the runner did not start again; log:\n%s", readFile(t, logA))
		}
		waitStatus(t, hc, "GET", base+"/a/api/runner", bearer(readA), 200, 60*time.Second)
		if got := lockPid(t, dirA); got != pid {
			t.Errorf("pid %d after the execv restart, want %d (the same process)", got, pid)
		}
	})

	t.Run("a second flyballd over the same manifests", func(t *testing.T) {
		pid, key := lockPid(t, dirA), readFile(t, filepath.Join(dirA, "key"))
		cfg2 := e.write("flyballd2.yaml", "listen: 127.0.0.1:0\nmanifests_dir: manifests\ndata_dir: data2\n")
		mgmt2 := e.createToken(cfg2, "mgmt2", "manage")
		d2 := e.start("flyballd2", "flyballd", "--config", cfg2)
		base2 := "http://" + d2.waitOutput(`flyballd listening on (\S+) \(`, 60*time.Second)[1]
		var rs map[string]runnerRow
		for end := time.Now().Add(60 * time.Second); time.Now().Before(end); time.Sleep(250 * time.Millisecond) {
			rs = runners(t, base2, mgmt2)
			if rs["a"].Status == "busy" && rs["b"].Status == "busy" {
				break
			}
		}
		if rs["a"].Status != "busy" || rs["b"].Status != "busy" {
			t.Fatalf("the second flyballd's runners: %+v, want busy (exit 3)", rs)
		}
		if lockPid(t, dirA) != pid || readFile(t, filepath.Join(dirA, "key")) != key {
			t.Fatal("the first runner or its live key changed")
		}
		if r := do(t, hc, "GET", base+"/a/api/runner", "", bearer(readA)); r.Status != 200 {
			t.Fatalf("a through the first flyballd: %v", r)
		}
		if r := do(t, hc, "GET", base2+"/a/api/runner", "", nil); r.Status == 200 {
			t.Fatalf("a through the second flyballd: %v, want no runner", r)
		}
	})
}
