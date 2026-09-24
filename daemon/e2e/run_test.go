//go:build e2e

package e2e

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front"
	"flyballd/internal/principal"
)

// authInfo is GET <base>/api/auth, decoded.
func authInfo(t *testing.T, c *http.Client, base string, hdr h) front.AuthInfo {
	t.Helper()
	r := do(t, c, "GET", base+"/api/auth", "", hdr)
	if r.Status != 200 {
		t.Fatalf("GET /api/auth: %v", r)
	}
	var info front.AuthInfo
	r.json(t, &info)
	return info
}

func verbs(info front.AuthInfo) string { return strings.Join(info.Verbs, ",") }

// endpointOf is the runner's endpoint, from GET /api/runner.
func endpointOf(t *testing.T, c *http.Client, url string, hdr h) string {
	t.Helper()
	r := do(t, c, "GET", url, "", hdr)
	var info struct {
		Endpoint string `json:"endpoint"`
	}
	r.json(t, &info)
	return info.Endpoint
}

// stopReport is POST /api/rig/stop's answer (§WP0-9).
type stopReport struct {
	Actor struct {
		Sub, Sid, Kind, Via string
	} `json:"actor"`
	Reason             string                     `json:"reason"`
	Devices            map[string]json.RawMessage `json:"devices"`
	ProgramInterrupted bool                       `json:"program_interrupted"`
	ControllersManual  []string                   `json:"controllers_manual"`
	Interim            bool                       `json:"interim"`
	Latched            bool                       `json:"latched"`
}

// reset lets the rig stop's latch go, as a person (hdr must carry one): a stop is
// latched until reset, and a latched rig refuses a program's regulate. Nothing held
// (404) is fine too.
func reset(t *testing.T, c *http.Client, base string, hdr h) {
	t.Helper()
	if r := do(t, c, "POST", base+"/api/rig/reset", `{"cause":"stop"}`, hdr); r.Status != 200 && r.Status != 404 {
		t.Fatalf("reset: %v", r)
	}
}

// startProgram starts the ten-hour program through the front.
func startProgram(t *testing.T, c *http.Client, base string, hdr h) {
	t.Helper()
	r := do(t, c, "POST", base+"/api/programs/run?cancel=true", program, hdr)
	if r.Status != 200 || !strings.Contains(string(r.Body), `"running":true`) {
		t.Fatalf("starting the program: %v", r)
	}
}

// A runner given --front-dir without a usable key exits 4, before it takes
// its store's lock (so before any hardware), and binds nothing. It took
// runner.lock first (before it read the key) and let go of it.
func TestFrontDirWithoutKeyExits4(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	for name, key := range map[string]string{"no key": "", "short key": strings.Repeat("a", 63) + "\n"} {
		t.Run(name, func(t *testing.T) {
			sub := strings.ReplaceAll(name, " ", "-")
			rig := e.rig(sub, "")
			fd := e.path(sub, "fd")
			if err := os.Mkdir(fd, 0o700); err != nil {
				t.Fatal(err)
			}
			os.WriteFile(filepath.Join(fd, "aud"), []byte("run-deadbeef\n"), 0o600)
			os.WriteFile(filepath.Join(fd, "endpoint"), []byte("unix:"+filepath.Join(fd, "sock")+"\n"), 0o600)
			if key != "" {
				os.WriteFile(filepath.Join(fd, "key"), []byte(key), 0o600)
			}
			p := e.start("runner-"+sub, "flyball-runner", rig, "--front-dir", fd)
			if code := p.wait(90 * time.Second); code != 4 {
				t.Fatalf("exit %d, want 4 (FRONT_DIR); output:\n%s", code, p.output())
			}
			for _, f := range []string{e.path(sub, "oven.sqlite.lock"), filepath.Join(fd, "sock")} {
				if fileExists(f) {
					t.Errorf("%s exists: the runner went past the front-dir check", f)
				}
			}
			if held, err := frontdir.LockHeld(fd); err != nil || held {
				t.Errorf("runner.lock after exit 4: held %v, %v", held, err)
			}
		})
	}
}

// flyball run, local shape: the runner is reached only through the front
// over its socket; forged, foreign and expired principals are refused at
// the socket; Host, Origin and path rules at the front; the front's
// headers; stop by HTTP, by `flyball stop`, and by SIGUSR1 with the front
// killed, each audited; kill -9 and a respawn with a fresh key.
func TestRunLocal(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	// Rig-file credentials the fronted runner must ignore (F2).
	rig := e.rig("local", "runner:\n  auth: {token: rig-file-token-0123456789, anonymous: read}\n")
	store := e.path("local", "oven.sqlite")
	fr, base := e.flyballRun("front", rig, "--listen", "127.0.0.1:0")
	addr := hostPort(base)
	same := origin(base)
	waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 90*time.Second)
	sock := strings.TrimPrefix(endpointOf(t, hc, base+"/api/runner", nil), "unix:")
	dir := filepath.Dir(sock)

	t.Run("auth info", func(t *testing.T) {
		info := authInfo(t, hc, base, nil)
		if info.V != 2 || info.Shape != "local" || info.Scheme != "local" || verbs(info) != "operate,read" ||
			info.User == nil || info.User.ID != "local:console" || info.Rig != "oven" {
			t.Fatalf("/api/auth = %+v", info)
		}
	})

	t.Run("front-dir", func(t *testing.T) {
		st, err := os.Lstat(dir)
		if err != nil || st.Mode()&os.ModeSymlink != 0 || st.Mode().Perm() != 0o700 {
			t.Fatalf("front-dir %s: %v %v, want a 0700 directory", dir, st.Mode(), err)
		}
		for _, f := range []string{"key", "aud", "endpoint", "runner.lock"} {
			st, err := os.Stat(filepath.Join(dir, f))
			if err != nil || st.Mode().Perm() != 0o600 {
				t.Errorf("%s: %v %v, want 0600", f, st.Mode(), err)
			}
		}
		if aud := readFile(t, filepath.Join(dir, "aud")); !regexp.MustCompile(`^run-[0-9a-f]{8}$`).MatchString(aud) {
			t.Errorf("aud = %q, want run-<8 hex>", aud)
		}
	})

	t.Run("key in no argv, environment or log", func(t *testing.T) {
		key := readFile(t, filepath.Join(dir, "key"))
		for _, pid := range []int{lockPid(t, dir), fr.pid()} {
			for _, f := range []string{"cmdline", "environ"} {
				b, err := os.ReadFile(fmt.Sprintf("/proc/%d/%s", pid, f))
				if err != nil {
					t.Fatal(err)
				}
				if strings.Contains(string(b), key) {
					t.Errorf("the key is in /proc/%d/%s", pid, f)
				}
			}
		}
		if strings.Contains(fr.output(), key) {
			t.Error("the key is in the log")
		}
	})

	t.Run("the socket takes only a good principal", func(t *testing.T) {
		uc := unixClient(sock)
		url := "http://localhost/api/runner"
		now := time.Now()
		var wrong principal.Key
		cases := []struct {
			name, tok, code string
		}{
			{"none", "", ""},
			{"wrong key", mintWith(t, wrong, readFile(t, filepath.Join(dir, "aud")), []string{"operate", "read"}, now), "mac"},
			{"foreign aud (another runner's principal)", mint(t, dir, "run-00000000", []string{"operate", "read"}, now), "aud"},
			{"expired", mint(t, dir, "", []string{"operate", "read"}, now.Add(-10*time.Minute)), "expired"},
			{"unknown version", "v2" + strings.TrimPrefix(mint(t, dir, "", []string{"read"}, now), "v1"), "version"},
		}
		for _, c := range cases {
			var hdr h
			if c.tok != "" {
				hdr = h{principal.Header, c.tok}
			}
			// Credentials the runner must ignore when fronted (F2).
			hdr = append(hdr, "Authorization", "Bearer rig-file-token-0123456789", "Cookie", "flyball-bare-8000=ignored")
			r := do(t, uc, "GET", url+"?token=ignored", "", hdr)
			if r.Status != 401 {
				t.Errorf("%s: %v, want 401", c.name, r)
			}
			if got := r.Header.Get("X-Flyball-Principal-Error"); c.code != "" && got != c.code {
				t.Errorf("%s: error header %q, want %q", c.name, got, c.code)
			}
		}
		if r := do(t, uc, "GET", url, "", h{principal.Header, mint(t, dir, "", []string{"read"}, now)}); r.Status != 200 {
			t.Fatalf("a good principal: %v, want 200 (the check cannot bite)", r)
		}
		// A valid principal without the verb: 403 naming it.
		r := do(t, uc, "POST", "http://localhost/api/rig/stop", "{}", h{principal.Header, mint(t, dir, "", []string{"read"}, now)})
		if r.Status != 403 || !strings.Contains(string(r.Body), `"needed":"operate"`) {
			t.Fatalf("stop with read only: %v, want 403 needing operate", r)
		}
	})

	t.Run("host, origin and path", func(t *testing.T) {
		if r := do(t, hc, "GET", base+"/api/runner", "", h{"Host", "evil.example"}); r.Status != 403 {
			t.Errorf("Host evil.example: %v, want 403", r)
		}
		for name, o := range map[string]h{"none": nil, "foreign": {"Origin", "http://evil.example"}, "null": {"Origin", "null"}} {
			if r := do(t, hc, "POST", base+"/api/rig/stop", "{}", o); r.Status != 403 {
				t.Errorf("stop with Origin %s: %v, want 403", name, r)
			}
		}
		w, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", h{"Origin", "http://evil.example"})
		if res.StatusCode == 101 {
			if code, _ := w.waitClose(3 * time.Second); code != 4401 && code != 4403 {
				t.Errorf("websocket from a foreign Origin: close %d", code)
			}
		} else if res.StatusCode != 403 {
			t.Errorf("websocket from a foreign Origin: %s, want refused", res.Status)
		}
		for _, p := range []string{"/api/%2e%2e/runner", "/api/..%2fauth", "/api/x%5cy", "/api/../api/runner", "/api/%2fx"} {
			if r := rawRequest(t, addr, "GET", p); r.Status != 400 {
				t.Errorf("GET %s: %v, want 400", p, r)
			}
		}
	})

	t.Run("headers", func(t *testing.T) {
		// Identity-looking headers from the client never reach the runner:
		// a second or underscored principal there would be refused (401,
		// which the front would answer 502).
		if r := do(t, hc, "GET", base+"/api/runner", "", h{"X-Flyball-Principal", "v1.forged.forged",
			"X_Flyball_Principal", "v1.forged.forged", "X-Flyball-Scopes", "operate", "X-Forwarded-For", "10.9.9.9",
			"Forwarded", "for=10.9.9.9", "X-Forwarded-Prefix", "/evil"}); r.Status != 200 {
			t.Errorf("with identity headers from the client: %v, want 200", r)
		}
		// A guarded path with no verb-table row is refused, not proxied through.
		if r := do(t, hc, "GET", base+"/api/no-such-route", "", nil); r.Status != 403 {
			t.Errorf("an unmapped /api path: %v, want 403", r)
		}
		r := do(t, hc, "GET", base+"/api/runner", "", nil)
		if r.Header.Get("X-Content-Type-Options") != "nosniff" || !strings.Contains(r.Header.Get("Content-Security-Policy"), "sandbox") {
			t.Errorf("proxied: %v", r.Header)
		}
		if r.Header.Get("Set-Cookie") != "" {
			t.Errorf("a runner's Set-Cookie reached the client: %v", r.Header)
		}
		page := do(t, hc, "GET", base+"/", "", nil)
		if !strings.Contains(page.Header.Get("Content-Security-Policy"), "frame-ancestors 'none'") || page.Header.Get("X-Frame-Options") != "DENY" {
			t.Errorf("the front's page: %v", page.Header)
		}
		if !strings.Contains(string(page.Body), "build-with-ui") {
			t.Errorf("a binary without the UI serves %q, want the page naming build-with-ui.sh", page.Body)
		}
		// Only /api, /ws and /mcp reach the runner: its /openapi.json does not.
		if r := do(t, hc, "GET", base+"/openapi.json", "", nil); strings.Contains(string(r.Body), `"openapi"`) {
			t.Errorf("/openapi.json was proxied: %v", r)
		}
	})

	t.Run("stop over HTTP", func(t *testing.T) {
		startProgram(t, hc, base, same)
		r := do(t, hc, "POST", base+"/api/rig/stop", `{"reason":"e2e-http"}`, same)
		if r.Status != 200 {
			t.Fatalf("stop: %v", r)
		}
		var rep stopReport
		r.json(t, &rep)
		// The oven's heater is a setpoint port: it declares no off, so the stop keeps it.
		if rep.Interim || !rep.Latched || !rep.ProgramInterrupted || rep.Actor.Sub != "local:console" || rep.Actor.Via != "http" ||
			len(rep.ControllersManual) == 0 || !strings.Contains(string(rep.Devices["heater"]), `"state":"unchanged"`) ||
			!strings.Contains(string(rep.Devices["heater"]), "no stop declared") {
			t.Fatalf("report: %s", r.Body)
		}
		fr.waitOutput(`software stop by local:console via http \(e2e-http\)`, 5*time.Second)
		waitAudit(t, store, map[string]string{"route": "/api/rig/stop", "sub": "local:console", "via": "http",
			"status": "200", "detail": "e2e-http"})
		for _, sql := range []string{"update audit set sub = 'x'", "delete from audit"} {
			out, err := execPython(store, sql)
			if err == nil || !strings.Contains(out, "append-only") {
				t.Errorf("%s: %v %s, want refused (append-only)", sql, err, out)
			}
		}
		// Never rate-limited.
		for i := range 25 {
			if r := do(t, hc, "POST", base+"/api/rig/stop", `{"reason":"burst"}`, same); r.Status != 200 {
				t.Fatalf("stop %d of a burst: %v", i, r)
			}
		}
	})

	t.Run("flyball stop through the front", func(t *testing.T) {
		reset(t, hc, base, same)
		startProgram(t, hc, base, same)
		out, errOut, code := e.run([]string{"FLYBALL_URL=" + base}, "flyball", "stop", "--reason", "e2e-cli")
		if code != 0 || !strings.Contains(out, "software stop: e2e-cli by local:console") || !strings.Contains(out, "program interrupted") {
			t.Fatalf("flyball stop: %d\n%s%s", code, out, errOut)
		}
		waitAudit(t, store, map[string]string{"route": "/api/rig/stop", "sub": "local:console", "detail": "e2e-cli"})
		// The runner's access log drops query strings (it shows `?…`).
		if strings.Contains(fr.output(), "interrupt=true") {
			t.Error("a query string is in the runner's access log")
		}
	})

	t.Run("kill -9, respawn, 200", func(t *testing.T) {
		pid, key, aud := lockPid(t, dir), readFile(t, filepath.Join(dir, "key")), readFile(t, filepath.Join(dir, "aud"))
		if err := syscall.Kill(pid, syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		deadline := time.Now().Add(60 * time.Second)
		for time.Now().Before(deadline) {
			b, _ := os.ReadFile(filepath.Join(dir, "runner.lock"))
			if f := strings.Fields(string(b)); len(f) > 1 && f[1] != fmt.Sprint(pid) {
				break
			}
			time.Sleep(200 * time.Millisecond)
		}
		waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 60*time.Second)
		if lockPid(t, dir) == pid {
			t.Fatal("no new runner")
		}
		if readFile(t, filepath.Join(dir, "key")) == key {
			t.Error("the respawned runner has the old key")
		}
		if readFile(t, filepath.Join(dir, "aud")) != aud {
			t.Error("the aud changed across a respawn")
		}
	})

	t.Run("SIGUSR1 with the front down", func(t *testing.T) {
		reset(t, hc, base, same)
		startProgram(t, hc, base, same)
		pid := lockPid(t, dir)
		syscall.Kill(fr.pid(), syscall.SIGKILL) // the front alone; the runner lives on
		fr.wait(10 * time.Second)
		if !alive(pid) {
			t.Fatal("the runner died with its front")
		}
		out, errOut, code := e.run([]string{"FLYBALL_URL=" + base}, "flyball", "stop", "--front-dir", dir)
		if code != 0 || !strings.Contains(out, fmt.Sprintf("sent SIGUSR1 to pid %d", pid)) {
			t.Fatalf("flyball stop with the front down: %d\n%s%s", code, out, errOut)
		}
		// The runner's own stdout/stderr (D-038's log tee) are a pipe the
		// dead front would have drained; with the front gone, nobody
		// reads it, so the break-glass report is confirmed from the
		// audit trail, not from `fr`'s own (already-dead) output.
		waitAudit(t, store, map[string]string{"method": "SIGNAL", "route": "SIGUSR1", "sub": "local:signal", "via": "signal", "outcome": "done"})
		time.Sleep(time.Second)
		if !alive(pid) {
			t.Fatal("SIGUSR1 ended the runner; it must stop the rig, not the process")
		}
	})
}

// mintWith is mint with an explicit key.
func mintWith(t *testing.T, k principal.Key, aud string, scp []string, now time.Time) string {
	t.Helper()
	tok, err := principal.Mint(k, principal.Claims{Sub: "local:console", Sid: "e2e-sid", Scp: scp, Kind: "human",
		Aud: aud, Sch: "http", Iat: now.Unix(), Exp: now.Add(principal.Lifetime).Unix()})
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

// login signs in with the admin password and returns the session cookie.
func login(t *testing.T, c *http.Client, base string) *http.Cookie {
	t.Helper()
	r := do(t, c, "POST", base+"/api/auth/login", fmt.Sprintf(`{"password":%q}`, password), origin(base))
	if r.Status != 200 {
		t.Fatalf("sign in: %v", r)
	}
	for _, line := range r.Header.Values("Set-Cookie") {
		if ck, err := http.ParseSetCookie(line); err == nil && ck.Value != "" {
			return ck
		}
	}
	t.Fatalf("sign in set no cookie: %v", r.Header)
	return nil
}

// newToken creates a named token over HTTP from the admin session.
func newToken(t *testing.T, base string, ck *http.Cookie, name string, scopes ...string) (secret, id string) {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"name": name, "scopes": scopes})
	r := do(t, hc, "POST", base+"/api/auth/tokens", string(body), cat(origin(base), h{"Cookie", ck.Name + "=" + ck.Value}))
	if r.Status != 201 {
		t.Fatalf("POST /api/auth/tokens %s: %v", name, r)
	}
	var out struct{ Token, ID string }
	r.json(t, &out)
	if !strings.HasPrefix(out.Token, "fbt1_") || out.ID == "" {
		t.Fatalf("created token: %s", r.Body)
	}
	return out.Token, out.ID
}

// flyball run, password shape with anonymous read: sign in, a session,
// named tokens made over HTTP and offline; each credential's websocket
// closed with 4401 within 1 s of logout or revocation, and refused with
// 4401 after; MCP through the front, where a read caller cannot actuate
// and an operator's inner call carries its name to the audit; stop with a
// named token by `flyball stop`; revocation leaves a running program be.
func TestRunPassword(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	rig := e.rig("pw", fmt.Sprintf("runner:\n  front:\n    auth: password\n    password: '%s'\n    anonymous: read\n", passwordLine))
	store := e.path("pw", "oven.sqlite")
	fr, base := e.flyballRun("front", rig, "--listen", "127.0.0.1:0")
	addr := hostPort(base)
	same := origin(base)
	waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 90*time.Second)
	var ck *http.Cookie
	cookie := func() h { return h{"Cookie", ck.Name + "=" + ck.Value} }

	t.Run("anonymous read", func(t *testing.T) {
		info := authInfo(t, hc, base, nil)
		if info.Shape != "password" || info.Scheme != "anonymous" || info.User != nil || verbs(info) != "read" ||
			info.Anonymous != "read" || !info.Login.Password {
			t.Fatalf("/api/auth = %+v", info)
		}
		if r := do(t, hc, "POST", base+"/api/rig/stop", "{}", same); r.Status != 401 {
			t.Fatalf("an anonymous stop: %v, want 401 (the runner's 403 for anonymous becomes 401)", r)
		}
		// A presented credential that does not work is refused, never
		// anonymous (tri-state), though anonymous would be let read.
		if r := do(t, hc, "GET", base+"/api/runner", "", bearer("fbt1_"+strings.Repeat("A", 43))); r.Status != 401 {
			t.Fatalf("an unknown token: %v, want 401", r)
		}
		// Identity headers from a client mean nothing in this shape.
		if info := authInfo(t, hc, base, h{"Remote-User", "ben", "X-Flyball-Principal", "v1.x.y"}); info.Scheme != "anonymous" {
			t.Fatalf("identity headers were believed: %+v", info)
		}
	})

	t.Run("sign in", func(t *testing.T) {
		r := do(t, hc, "POST", base+"/api/auth/login", `{"password":"wrong"}`, same)
		if r.Status != 401 || string(r.Body) != `{"detail":"Wrong password"}` {
			t.Fatalf("a wrong password: %v", r)
		}
		ck = login(t, hc, base)
		if !ck.HttpOnly || ck.SameSite != http.SameSiteLaxMode || ck.Path != "/" || ck.Secure || !strings.HasPrefix(ck.Name, "flyball-") {
			t.Fatalf("session cookie %+v, want flyball-<port>, HttpOnly, SameSite=Lax, Path=/, not Secure over plain HTTP", ck)
		}
		info := authInfo(t, hc, base, cookie())
		if info.Scheme != "session" || info.User == nil || info.User.ID != "local:admin" || verbs(info) != "operate,read" {
			t.Fatalf("/api/auth signed in = %+v", info)
		}
		// The session acts, and its sid is not derivable from the cookie.
		if r := do(t, hc, "POST", base+"/api/programs/cancel", "", cat(same, cookie())); r.Status != 200 {
			t.Fatalf("interrupt with the session: %v", r)
		}
		row := waitAudit(t, store, map[string]string{"route": "/api/programs/cancel", "sub": "local:admin", "status": "200"})
		if sid := row.s("sid"); sid == "" || strings.Contains(ck.Value, sid) || strings.Contains(sid, ck.Value) {
			t.Fatalf("the audit's sid %q and the cookie %q are related", sid, ck.Value)
		}
		// The management scope is never issued over HTTP.
		r = do(t, hc, "POST", base+"/api/auth/tokens", `{"name":"m","scopes":["manage"]}`, cat(same, cookie()))
		if r.Status != 403 {
			t.Fatalf("a manage token over HTTP: %v, want 403", r)
		}
	})

	t.Run("the cookie is named after the port the front serves", func(t *testing.T) {
		// Known defect: cookieName (internal/front/auth.go) reads the
		// configured listen port, so --listen 127.0.0.1:0 names every such
		// front's cookie flyball-0, and two of them on one host share it.
		if _, port, _ := strings.Cut(addr, ":"); ck.Name != "flyball-"+port {
			t.Errorf("session cookie %q, want flyball-%s", ck.Name, port)
		}
	})

	readTok, readID := newToken(t, base, ck, "e2e-read", "read")
	opTok, opID := newToken(t, base, ck, "e2e-op", "operate")

	t.Run("no credential in a URL; the session's Origin is checked", func(t *testing.T) {
		if r := do(t, hc, "POST", base+"/api/rig/stop?token="+opTok, "{}", same); r.Status != 401 {
			t.Errorf("an operate token in the query: %v, want 401 (anonymous)", r)
		}
		for name, o := range map[string]h{"none": nil, "foreign": {"Origin", "http://evil.example"}, "null": {"Origin", "null"}} {
			if r := do(t, hc, "POST", base+"/api/rig/stop", "{}", cat(cookie(), o)); r.Status != 403 {
				t.Errorf("the session stopping with Origin %s: %v, want 403", name, r)
			}
		}
		// A bearer token is exempt: no ambient credential to ride on.
		if r := do(t, hc, "POST", base+"/api/programs/cancel", "", cat(bearer(opTok), h{"Origin", "http://evil.example"})); r.Status != 200 {
			t.Errorf("a bearer token with a foreign Origin: %v, want 200", r)
		}
	})

	t.Run("a read token", func(t *testing.T) {
		if r := do(t, hc, "GET", base+"/api/runner", "", bearer(readTok)); r.Status != 200 {
			t.Fatalf("read with a read token: %v", r)
		}
		r := do(t, hc, "POST", base+"/api/rig/stop", `{"reason":"not allowed"}`, bearer(readTok))
		if r.Status != 403 || !strings.Contains(string(r.Body), `"needed":"operate"`) {
			t.Fatalf("stop with a read token: %v, want 403 needing operate", r)
		}
		waitAudit(t, store, map[string]string{"route": "/api/rig/stop", "sub": "token:e2e-read", "status": "403", "outcome": "denied"})
		if r := do(t, hc, "GET", base+"/api/auth/tokens", "", bearer(readTok)); r.Status != 403 {
			t.Fatalf("token routes with a token: %v, want 403", r)
		}
	})

	t.Run("MCP through the front", func(t *testing.T) {
		rd := &mcp{t: t, url: base + "/mcp/read", hdr: bearer(readTok)}
		if st := rd.initialize(); st != 200 {
			t.Fatalf("initialize /mcp/read with a read token: %d", st)
		}
		tools := rd.tools()
		for _, bad := range []string{"stop_rig", "demand", "check_driver", "search_drivers"} {
			if contains(tools, bad) {
				t.Errorf("/mcp/read offers %s: %v", bad, tools)
			}
		}
		if text, isErr := rd.tool("read", map[string]any{"address": "thermocouple.temperature"}); isErr {
			t.Fatalf("read over MCP: %s", text)
		}

		// A read caller at the operate mode is refused by the runner, and
		// the refusal names the token.
		up := &mcp{t: t, url: base + "/mcp/operate", hdr: bearer(readTok)}
		if st := up.initialize(); st != 403 {
			t.Fatalf("/mcp/operate with a read token: %d, want 403", st)
		}
		waitAudit(t, store, map[string]string{"route": "/mcp/operate", "sub": "token:e2e-read", "outcome": "denied"})

		op := &mcp{t: t, url: base + "/mcp/operate", hdr: bearer(opTok)}
		if st := op.initialize(); st != 200 {
			t.Fatalf("/mcp/operate with an operate token: %d", st)
		}
		tools = op.tools()
		if !contains(tools, "stop_rig") || contains(tools, "check_driver") || contains(tools, "search_drivers") {
			t.Fatalf("/mcp/operate tools: %v", tools)
		}
		if text, isErr := op.tool("stop_rig", map[string]any{"reason": "e2e-mcp"}); isErr {
			t.Fatalf("stop_rig: %s", text)
		}
		// The tool's inner call carries the caller, re-minted, via mcp.
		waitAudit(t, store, map[string]string{"route": "/api/rig/stop", "sub": "token:e2e-op", "via": "mcp",
			"status": "200", "detail": "e2e-mcp"})
	})

	t.Run("token revoked: its websocket closes 4401 within 1 s", func(t *testing.T) {
		w, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", bearer(readTok))
		if res.StatusCode != 101 {
			t.Fatalf("websocket with a read token: %s", res.Status)
		}
		w.open(t, 10*time.Second)
		start := time.Now()
		if r := do(t, hc, "DELETE", base+"/api/auth/tokens/"+readID, "", cat(same, cookie())); r.Status != 204 {
			t.Fatalf("revoke: %v", r)
		}
		code, _ := w.waitClose(3 * time.Second)
		if took := time.Since(start); code != 4401 || took > time.Second {
			t.Fatalf("after revocation: close %d after %s, want 4401 within 1 s", code, took)
		}
		w2, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", bearer(readTok))
		if res.StatusCode != 101 {
			t.Fatalf("a revoked token's upgrade: %s, want 101 then close 4401", res.Status)
		}
		if code, _ := w2.waitClose(3 * time.Second); code != 4401 {
			t.Fatalf("a revoked token's upgrade closed %d, want 4401", code)
		}
		if r := do(t, hc, "GET", base+"/api/runner", "", bearer(readTok)); r.Status != 401 {
			t.Fatalf("a revoked token: %v, want 401", r)
		}
	})

	t.Run("revoking a principal leaves the program running", func(t *testing.T) {
		reset(t, hc, base, cat(same, cookie())) // a token may not reset: a person does
		startProgram(t, hc, base, bearer(opTok))
		waitAudit(t, store, map[string]string{"route": "/api/programs/run", "sub": "token:e2e-op", "status": "200"})
		before := do(t, hc, "GET", base+"/api/programs/running", "", nil)
		if r := do(t, hc, "DELETE", base+"/api/auth/tokens/"+opID, "", cat(same, cookie())); r.Status != 204 {
			t.Fatalf("revoke: %v", r)
		}
		time.Sleep(1500 * time.Millisecond)
		after := do(t, hc, "GET", base+"/api/programs/running", "", nil)
		if !strings.Contains(string(after.Body), `"running":true`) || string(after.Body) != string(before.Body) {
			t.Fatalf("the program before revocation %s, after %s", before.Body, after.Body)
		}
		if r := do(t, hc, "POST", base+"/api/programs/cancel", "", bearer(opTok)); r.Status != 401 {
			t.Fatalf("a revoked operate token acting: %v, want 401", r)
		}
	})

	t.Run("an offline token: flyball stop, then flyball token revoke", func(t *testing.T) {
		secret, errOut, code := e.run(nil, "flyball", "token", "create", "--config", rig, "--name", "cli-op", "--scope", "operate")
		secret = strings.TrimSpace(secret)
		m := regexp.MustCompile(`token (\S+):`).FindStringSubmatch(errOut)
		if code != 0 || !strings.HasPrefix(secret, "fbt1_") || m == nil {
			t.Fatalf("flyball token create: %d %q %s", code, secret, errOut)
		}
		reset(t, hc, base, cat(same, cookie()))
		startProgram(t, hc, base, bearer(secret))
		out, errOut, code := e.run([]string{"FLYBALL_URL=" + base}, "flyball", "--token", secret, "stop", "--reason", "e2e-token")
		if code != 0 || !strings.Contains(out, "software stop: e2e-token by token:cli-op") || !strings.Contains(out, "program interrupted") {
			t.Fatalf("flyball stop with a token: %d\n%s%s", code, out, errOut)
		}
		waitAudit(t, store, map[string]string{"route": "/api/rig/stop", "sub": "token:cli-op", "kind": "service", "detail": "e2e-token"})

		w, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", bearer(secret))
		if res.StatusCode != 101 {
			t.Fatalf("websocket with the offline token: %s", res.Status)
		}
		w.open(t, 10*time.Second)
		if _, errOut, code := e.run(nil, "flyball", "token", "revoke", m[1], "--config", rig); code != 0 {
			t.Fatalf("flyball token revoke: %d %s", code, errOut)
		}
		start := time.Now()
		code, _ = w.waitClose(5 * time.Second)
		if took := time.Since(start); code != 4401 || took > time.Second {
			t.Errorf("after `flyball token revoke`: close %d after %s, want 4401 within 1 s", code, took)
		}
		if r := do(t, hc, "GET", base+"/api/runner", "", bearer(secret)); r.Status != 401 {
			t.Fatalf("the revoked offline token: %v, want 401", r)
		}
	})

	t.Run("sign out: the session's websocket closes 4401 within 1 s", func(t *testing.T) {
		w, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", cat(same, cookie()))
		if res.StatusCode != 101 {
			t.Fatalf("websocket with the session: %s", res.Status)
		}
		w.open(t, 10*time.Second)
		start := time.Now()
		r := do(t, hc, "POST", base+"/api/auth/logout", "", cat(same, cookie()))
		if r.Status != 200 {
			t.Fatalf("sign out: %v", r)
		}
		code, _ := w.waitClose(3 * time.Second)
		if took := time.Since(start); code != 4401 || took > time.Second {
			t.Fatalf("after sign out: close %d after %s, want 4401 within 1 s", code, took)
		}
		if info := authInfo(t, hc, base, cookie()); info.Scheme != "anonymous" {
			t.Fatalf("/api/auth with the old cookie: %+v", info)
		}
		if r := do(t, hc, "GET", base+"/api/runner", "", cookie()); r.Status != 401 {
			t.Fatalf("a guarded route with the old cookie: %v, want 401", r)
		}
		w2, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", cat(same, cookie()))
		if res.StatusCode != 101 {
			t.Fatalf("the old cookie's upgrade: %s, want 101 then 4401", res.Status)
		}
		if code, _ := w2.waitClose(3 * time.Second); code != 4401 {
			t.Fatalf("the old cookie's upgrade closed %d, want 4401", code)
		}
	})

	t.Run("token expiry: its websocket closes 4401 within 1 s", func(t *testing.T) {
		ck = login(t, hc, base) // signed out above
		r := do(t, hc, "POST", base+"/api/auth/tokens", `{"name":"short","scopes":["read"],"expires_in":3}`, cat(same, cookie()))
		if r.Status != 201 {
			t.Fatalf("a 3 s token: %v", r)
		}
		var tok struct {
			Token   string
			Expires time.Time
		}
		r.json(t, &tok)
		w, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", bearer(tok.Token))
		if res.StatusCode != 101 {
			t.Fatalf("websocket with the 3 s token: %s", res.Status)
		}
		w.open(t, 5*time.Second)
		code, _ := w.waitClose(10 * time.Second)
		late := time.Since(tok.Expires)
		if code != 4401 || late > time.Second+250*time.Millisecond {
			t.Fatalf("on expiry: close %d, %s after the expiry, want 4401 within 1 s", code, late)
		}
		t.Logf("closed %s after the token's expiry", late.Round(time.Millisecond))
	})

	t.Run("the front's audit", func(t *testing.T) {
		files, _ := filepath.Glob(e.path("state", "flyball", "front-*", "audit.jsonl"))
		if len(files) != 1 {
			t.Fatalf("audit.jsonl: %v", files)
		}
		st, _ := os.Stat(files[0])
		if st.Mode().Perm() != 0o600 {
			t.Errorf("audit.jsonl is %v, want 0600", st.Mode().Perm())
		}
		b := readFile(t, files[0])
		for _, ev := range []string{"login.fail", "login.ok", "token.create", "token.revoke", "logout"} {
			if !strings.Contains(b, `"`+ev+`"`) {
				t.Errorf("no %s event in the front's audit", ev)
			}
		}
		if strings.Contains(b, password) || strings.Contains(b, opTok) {
			t.Error("a secret is in the front's audit")
		}
	})
	_ = fr
}

// TLS from a certificate file: the front serves it, the cookie is
// __Host-flyball and Secure, and a renewed pair is served to new
// connections (on SIGHUP, and on the periodic re-stat) without a restart;
// a bad renewal keeps the last good pair.
func TestRunTLS(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	cert, key := e.path("tls", "cert.pem"), e.path("tls", "key.pem")
	os.MkdirAll(e.path("tls"), 0o700)
	c1 := writeCert(t, cert, key, 1)
	rig := e.rig("tls", fmt.Sprintf("runner:\n  front:\n    auth: password\n    password: '%s'\n    tls: {cert: %s, key: %s}\n",
		passwordLine, cert, key))
	fr, base := e.flyballRun("front", rig, "--listen", "127.0.0.1:0")
	if !strings.HasPrefix(base, "https://") {
		t.Fatalf("the front serves %s, want https", base)
	}
	addr := hostPort(base)
	pool := x509Pool(c1)
	tc := tlsClient(pool)
	waitStatus(t, tc, "GET", base+"/api/auth", nil, 200, 30*time.Second)
	if s, err := servedSerial(t, addr, pool); err != nil || s != 1 {
		t.Fatalf("serial %d %v, want 1", s, err)
	}
	if _, err := try(hc, "GET", "http://"+addr+"/api/auth", "", nil); err == nil {
		if r, _ := try(hc, "GET", "http://"+addr+"/api/auth", "", nil); r.Status == 200 {
			t.Fatal("plain HTTP answered 200 on the TLS listener")
		}
	}

	t.Run("sign in over TLS", func(t *testing.T) {
		waitStatus(t, tc, "GET", base+"/api/runner", h{"Authorization", "Bearer x"}, 401, 5*time.Second)
		ck := login(t, tc, base)
		if ck.Name != "__Host-flyball" || !ck.Secure || !ck.HttpOnly || ck.Path != "/" {
			t.Fatalf("cookie %+v, want __Host-flyball, Secure, HttpOnly, Path=/", ck)
		}
		waitStatus(t, tc, "GET", base+"/api/runner", h{"Cookie", ck.Name + "=" + ck.Value}, 200, 90*time.Second)
	})

	serial := func(want int64, d time.Duration) {
		t.Helper()
		var got int64
		var err error
		for end := time.Now().Add(d); time.Now().Before(end); time.Sleep(250 * time.Millisecond) {
			if got, err = servedSerial(t, addr, pool); err == nil && got == want {
				return
			}
		}
		t.Fatalf("serial %d (%v), want %d within %s", got, err, want, d)
	}

	t.Run("renewal on SIGHUP", func(t *testing.T) {
		pool.AddCert(writeCert(t, cert, key, 2))
		syscall.Kill(fr.pid(), syscall.SIGHUP)
		serial(2, 5*time.Second)
		if fr.exited() {
			t.Fatal("SIGHUP stopped flyball run")
		}
	})
	t.Run("renewal by re-stat, no signal", func(t *testing.T) {
		time.Sleep(1100 * time.Millisecond) // a new mtime second
		pool.AddCert(writeCert(t, cert, key, 3))
		serial(3, 15*time.Second)
	})
	t.Run("a bad renewal keeps the last good pair", func(t *testing.T) {
		atomicWrite(t, cert, []byte("not a certificate\n"), 0o644)
		syscall.Kill(fr.pid(), syscall.SIGHUP)
		time.Sleep(2 * time.Second)
		if s, err := servedSerial(t, addr, pool); err != nil || s != 3 {
			t.Fatalf("after a bad renewal: serial %d %v, want 3", s, err)
		}
		if r := do(t, tc, "GET", base+"/api/auth", "", nil); r.Status != 200 {
			t.Fatalf("after a bad renewal: %v", r)
		}
	})
}

// D-038: a hangup (SIGHUP to the whole foreground process group, as a
// terminal that goes away sends it) never stops `flyball run` or its
// runner, bare (no TLS) as well as under TLS (TestRunTLS's "renewal on
// SIGHUP" covers the TLS reload half). The front answers after, the
// runner is still alive with the same pid, and run.log carries output
// written after the hangup. A later SIGTERM (Ctrl-C's signal) still
// stops both cleanly.
func TestRunSurvivesHangup(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	rig := e.rig("hup", "")
	fr, base := e.flyballRun("front", rig, "--listen", "127.0.0.1:0")
	waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 90*time.Second)
	sock := strings.TrimPrefix(endpointOf(t, hc, base+"/api/runner", nil), "unix:")
	dir := filepath.Dir(sock)
	pid := lockPid(t, dir)

	// `flyball run` itself is the group leader (e.start's Setpgid); a
	// hangup delivers SIGHUP to every process in that group, the front
	// included -- exactly what a dropped terminal does.
	if err := syscall.Kill(-fr.pid(), syscall.SIGHUP); err != nil {
		t.Fatal(err)
	}
	fr.waitOutput(`terminal hung up; the rig keeps running`, 5*time.Second)
	if fr.exited() {
		t.Fatal("SIGHUP stopped flyball run")
	}
	if !alive(pid) {
		t.Fatal("SIGHUP reached the runner: it is in its own process group (D-038)")
	}
	if lockPid(t, dir) != pid {
		t.Fatal("the runner was respawned: SIGHUP reached it")
	}
	waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 10*time.Second)

	matches, err := filepath.Glob(e.path("state", "flyball", "front-*", "run.log"))
	if err != nil || len(matches) != 1 {
		t.Fatalf("run.log: %v %v", matches, err)
	}
	if st, err := os.Stat(matches[0]); err != nil || st.Mode().Perm() != 0o600 {
		t.Fatalf("run.log %s: %v %v, want 0600", matches[0], st, err)
	}
	if b, err := os.ReadFile(matches[0]); err != nil || !strings.Contains(string(b), "terminal hung up") {
		t.Fatalf("run.log = %q (%v), want the post-hangup line", b, err)
	}

	fr.stop(10 * time.Second) // SIGTERM to the group: the runner's clean shutdown
	if !fr.exited() {
		t.Fatal("flyball run did not stop on SIGTERM after a hangup")
	}
	if alive(pid) {
		t.Fatal("the runner outlived SIGTERM")
	}
}

// The proxy shape with the authelia preset: the front listens on a unix
// socket and a stand-in authenticating proxy (as Authelia's forward-auth
// behind a reverse proxy would) asserts Remote-User over it. The
// identity is proxy:<issuer>#<subject>, grants decide the verbs, an
// unmatched user gets read, and the email is never a subject. The same
// headers sent over TCP to a front that does not vouch for the peer are
// ignored.
func TestRunProxy(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	sockPath := e.path("front.sock")
	rig := e.rig("proxy", fmt.Sprintf(`runner:
  front:
    listen: unix:%s
    auth: proxy
    proxy:
      preset: authelia
      from: unix
      grants: {all: [ben, "ben@lab.example"]}
`, sockPath))
	store := e.path("proxy", "oven.sqlite")
	_, where := e.flyballRun("front", rig)
	if where != "unix:"+sockPath {
		t.Fatalf("the front serves %s, want unix:%s", where, sockPath)
	}
	px := autheliaProxy(t, sockPath)
	as := func(user string) h { return h{"X-Test-User", user} }
	waitStatus(t, hc, "GET", px+"/api/runner", as("ben"), 200, 90*time.Second)

	ben := authInfo(t, hc, px, as("ben"))
	if ben.Shape != "proxy" || ben.Scheme != "proxy" || ben.User == nil || !strings.HasPrefix(ben.User.ID, "proxy:") ||
		!strings.HasSuffix(ben.User.ID, "#ben") || verbs(ben) != "operate,read" {
		t.Fatalf("ben: %+v", ben)
	}
	if eve := authInfo(t, hc, px, as("eve")); verbs(eve) != "read" || eve.User == nil || !strings.HasSuffix(eve.User.ID, "#eve") {
		t.Fatalf("eve, matching no grant: %+v, want read", eve)
	}
	if anon := authInfo(t, hc, px, nil); anon.Scheme != "anonymous" || verbs(anon) != "" {
		t.Fatalf("no identity: %+v", anon)
	}
	if r := do(t, hc, "GET", px+"/api/runner", "", nil); r.Status != 401 {
		t.Fatalf("no identity, anonymous none: %v, want 401", r)
	}
	if r := do(t, hc, "POST", px+"/api/rig/stop", `{"reason":"eve"}`, cat(as("eve"), origin(px))); r.Status != 403 {
		t.Fatalf("eve stops: %v, want 403", r)
	}
	if r := do(t, hc, "POST", px+"/api/rig/stop", `{"reason":"e2e-proxy"}`, cat(as("ben"), origin(px))); r.Status != 200 {
		t.Fatalf("ben stops: %v", r)
	}
	waitAudit(t, store, map[string]string{"route": "/api/rig/stop", "sub": ben.User.ID, "detail": "e2e-proxy"})
	// The email is display only: it never names the subject, and a grant
	// listing an email grants nothing through it.
	if info := authInfo(t, hc, px, h{"X-Test-Email", "ben@lab.example"}); info.Scheme != "anonymous" {
		t.Fatalf("an email with no user: %+v, want anonymous", info)
	}
	if info := authInfo(t, hc, px, h{"X-Test-User", "eve", "X-Test-Email", "ben@lab.example"}); verbs(info) != "read" {
		t.Fatalf("eve with a granted email: %+v, want read", info)
	}

	t.Run("over TCP from an unvouched peer, ignored", func(t *testing.T) {
		rig := e.rig("proxytcp", `runner:
  front:
    auth: proxy
    anonymous: read
    proxy:
      preset: authelia
      from: ["192.0.2.10"]
      grants: {all: [ben]}
`)
		_, base := e.flyballRun("front-tcp", rig, "--listen", "127.0.0.1:0")
		waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 90*time.Second)
		info := authInfo(t, hc, base, h{"Remote-User", "ben", "Remote-Groups", "admins"})
		if info.Shape != "proxy" || info.Scheme != "anonymous" || verbs(info) != "read" {
			t.Fatalf("Remote-User from 127.0.0.1: %+v, want anonymous read", info)
		}
		if r := do(t, hc, "POST", base+"/api/rig/stop", "{}", cat(h{"Remote-User", "ben"}, origin(base))); r.Status != 401 {
			t.Fatalf("stop as a forged ben: %v, want 401", r)
		}
	})
}

// D-028: every front misconfiguration still runs the rig, served by the
// local shape on loopback with a banner saying why; so do the runner's
// removed flags.
func TestRunFallbacks(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, front string
		listen      string
		extra       []string
		banner      string
	}{
		{"plaintext password", "auth: password\n    password: hunter2", "127.0.0.1:0", nil, "plaintext passwords are refused"},
		{"sso", "auth: sso", "127.0.0.1:0", nil, "sso is not in this release"},
		{"password shape with no password", "auth: password", "127.0.0.1:0", nil, "needs a password"},
		{"bad TLS files", "auth: password\n    password: '" + passwordLine + "'\n    tls: {cert: /nonexistent/c.pem, key: /nonexistent/k.pem}", "127.0.0.1:0", nil, "tls:"},
		{"unvouched proxy", "auth: proxy\n    proxy: {preset: authelia, from: ['127.0.0.1']}", "127.0.0.1:0", nil, "secret_file"},
		{"non-loopback local", "", "0.0.0.0:0", nil, "is not loopback"},
		{"invalid front block", "bogus_key: 1", "127.0.0.1:0", nil, "cannot be read"},
		{"unknown shape", "auth: kerberos", "127.0.0.1:0", nil, "is not a shape"},
		{"removed runner flags", "", "127.0.0.1:0", []string{"--password", "hunter2", "--session", "1h"}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			e := newEnv(t)
			extra := ""
			if c.front != "" {
				extra = "runner:\n  front:\n    " + c.front + "\n"
			}
			rig := e.rig("rig", extra)
			fr, base := e.flyballRun("front", rig, append([]string{"--listen", c.listen}, c.extra...)...)
			if !strings.HasPrefix(base, "http://127.0.0.1:") {
				t.Fatalf("served on %s, want loopback", base)
			}
			waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 90*time.Second)
			info := authInfo(t, hc, base, nil)
			if info.Shape != "local" {
				t.Fatalf("/api/auth = %+v, want the local shape", info)
			}
			if c.banner == "" {
				fr.waitOutput(`(?i)--password.*ignored|ignored.*--password|password.*removed`, 10*time.Second)
				return
			}
			if !strings.Contains(fr.output(), "D-028") || !strings.Contains(fr.output(), c.banner) {
				t.Fatalf("no D-028 banner naming %q; output:\n%s", c.banner, fr.output())
			}
			if info.Exposure == nil || info.Exposure.Warning == nil || !strings.Contains(*info.Exposure.Warning, c.banner) {
				t.Fatalf("/api/auth exposure = %+v, want the warning", info.Exposure)
			}
		})
	}
}

// A session idle past its lifetime (session: 2s, a reference key) ends,
// and its open websocket is closed 4401 within 1 s of that.
func TestRunSessionExpiry(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	rig := e.rig("sx", fmt.Sprintf("runner:\n  front:\n    auth: password\n    password: '%s'\n    session: 2s\n", passwordLine))
	_, base := e.flyballRun("front", rig, "--listen", "127.0.0.1:0")
	addr := hostPort(base)
	ck := login(t, hc, base)
	cookie := h{"Cookie", ck.Name + "=" + ck.Value}
	waitStatus(t, hc, "GET", base+"/api/runner", cookie, 200, 90*time.Second)
	w, res := dialWS(t, tcpDial(addr), addr, "/ws/samples", cat(origin(base), cookie))
	if res.StatusCode != 101 {
		t.Fatalf("websocket with the session: %s", res.Status)
	}
	opened := time.Now()
	w.open(t, 5*time.Second)
	code, took := w.waitClose(10 * time.Second)
	if code != 4401 {
		t.Fatalf("an idle session's websocket: close %d, want 4401", code)
	}
	// Idle 2 s, then at most 1 s to close it.
	if since := time.Since(opened); since > 3*time.Second+250*time.Millisecond {
		t.Fatalf("closed %s after the session's last use, want within idle (2 s) + 1 s", since)
	}
	t.Logf("closed after %s", took.Round(time.Millisecond))
	if r := do(t, hc, "GET", base+"/api/runner", "", cookie); r.Status != 401 {
		t.Fatalf("the expired session: %v, want 401", r)
	}
}

// Credentials on a listen address beyond loopback without TLS: the front
// serves the shape asked for and warns that it is cleartext.
func TestRunCleartextWarning(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	rig := e.rig("ct", fmt.Sprintf("runner:\n  front:\n    auth: password\n    password: '%s'\n", passwordLine))
	fr, base := e.flyballRun("front", rig, "--listen", "0.0.0.0:0")
	_, port, err := net.SplitHostPort(hostPort(base))
	if err != nil {
		t.Fatal(err)
	}
	local := "http://127.0.0.1:" + port
	info := authInfo(t, hc, local, nil)
	if info.Shape != "password" {
		t.Fatalf("/api/auth = %+v, want the password shape", info)
	}
	if !strings.Contains(fr.output(), "serving plain HTTP on") {
		t.Fatalf("no cleartext warning at start; output:\n%s", fr.output())
	}
	if info.Exposure == nil || info.Exposure.Warning == nil || !strings.Contains(*info.Exposure.Warning, "plain HTTP") {
		t.Fatalf("/api/auth exposure = %+v, want the cleartext warning", info.Exposure)
	}
}

// oldRunner writes a flyball-runner stand-in into dir: with open, one that
// binds the endpoint its front-dir names and answers everything 200 (a
// runner that does not enforce the principal); without, one that refuses
// --front-dir as an unknown flag (exit 2), as a runner older than it would.
func oldRunner(t *testing.T, dir string, open bool) {
	t.Helper()
	script := `#!` + filepath.Join(venvBin, "python") + `
import sys
if ` + map[bool]string{true: "False", false: "True"}[open] + `:
    print("flyball-runner: error: unrecognized arguments: --front-dir", file=sys.stderr)
    sys.exit(2)
import os, socketserver, http.server
d = sys.argv[sys.argv.index("--front-dir") + 1]
sock = open(os.path.join(d, "endpoint")).read().strip().removeprefix("unix:")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        print("old runner served", self.path, flush=True)
        b = b'{"protocol": 1, "aud": "whatever"}'
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
    def address_string(self): return "unix"
class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
S(sock, H).serve_forever()
`
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "flyball-runner"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
}

// A runner that does not enforce the principal is never proxied to: the
// front answers 502 "runner too old"; one that refuses --front-dir ends
// the run with that said.
func TestRunOldRunner(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	path := func(dir string) []string {
		return []string{"PATH=" + dir + string(os.PathListSeparator) + binDir + string(os.PathListSeparator) + os.Getenv("PATH")}
	}
	t.Run("answers without a principal", func(t *testing.T) {
		oldRunner(t, e.path("open-bin"), true)
		rig := e.rig("open", "")
		p := e.startIn(e.dir, path(e.path("open-bin")), "front-open", "flyball", "run", rig, "--listen", "127.0.0.1:0")
		base := strings.TrimSuffix(p.waitOutput(`flyball: serving rig \S+ on (\S+) \(`, 60*time.Second)[1], "/")
		r := waitStatus(t, hc, "GET", base+"/api/runner", nil, 502, 30*time.Second)
		if !strings.Contains(string(r.Body), "too old") {
			t.Fatalf("GET /api/runner: %v, want 502 runner too old", r)
		}
		if m := regexp.MustCompile(`old runner served (\S+)`).FindAllStringSubmatch(p.output(), -1); len(m) == 0 {
			t.Fatal("the stand-in was never probed: the test cannot bite")
		} else {
			for _, x := range m {
				if !strings.HasPrefix(x[1], "/api/auth/front") {
					t.Errorf("the front proxied %s to a runner that does not enforce the principal", x[1])
				}
			}
		}
	})
	t.Run("refuses --front-dir", func(t *testing.T) {
		oldRunner(t, e.path("old-bin"), false)
		rig := e.rig("old", "")
		p := e.startIn(e.dir, path(e.path("old-bin")), "front-old", "flyball", "run", rig, "--listen", "127.0.0.1:0")
		if code := p.wait(30 * time.Second); code == 0 {
			t.Fatalf("flyball run with a runner too old: exit 0")
		}
		if !strings.Contains(p.output(), "too old for --front-dir") {
			t.Fatalf("no word of a runner too old; output:\n%s", p.output())
		}
	})
}

// flyball run, no flyballd: a rig edit through the front (D-051) saves the
// change beside the rig file, and the runner restarts itself by execv --
// the same pid, the front still in front, the new device in the rig -- with
// no --allow-shutdown.
func TestRunRigEditRestarts(t *testing.T) {
	t.Parallel()
	e := newEnv(t)
	rig := e.rig("edit", "")
	_, base := e.flyballRun("front", rig, "--listen", "127.0.0.1:0")
	same := origin(base)
	waitStatus(t, hc, "GET", base+"/api/runner", nil, 200, 90*time.Second)
	dir := filepath.Dir(strings.TrimPrefix(endpointOf(t, hc, base+"/api/runner", nil), "unix:"))
	pid := lockPid(t, dir)

	probe := `{"name": "probe2", "driver": "sim_daq", "link": "chamber",` +
		` "ports": {"temperature": {"port": "output", "quantity": "temperature", "unit": "°C"}}}`
	r := do(t, hc, "POST", base+"/api/devices", probe, same)
	if r.Status != 202 {
		t.Fatalf("POST /api/devices: %v, want 202", r)
	}
	var out struct {
		Version    int    `json:"version"`
		Saved      string `json:"saved"`
		Restarting bool   `json:"restarting"`
	}
	r.json(t, &out)
	if !out.Restarting || !strings.HasSuffix(out.Saved, "oven.yaml.d/added.yaml") || !fileExists(out.Saved) {
		t.Fatalf("the edit's answer: %+v", out)
	}
	deadline := time.Now().Add(60 * time.Second)
	for {
		if got, err := try(hc, "GET", base+"/api/rig/document", "", nil); err == nil && got.Status == 200 &&
			strings.Contains(string(got.Body), `"probe2"`) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the runner never came back with probe2")
		}
		time.Sleep(200 * time.Millisecond)
	}
	if got := lockPid(t, dir); got != pid {
		t.Errorf("pid %d after the edit's restart, want %d (execv: the same process)", got, pid)
	}
	var versions []struct {
		ID   int  `json:"id"`
		Head bool `json:"head"`
	}
	do(t, hc, "GET", base+"/api/rig/versions", "", nil).json(t, &versions)
	if len(versions) == 0 || versions[0].ID != out.Version || !versions[0].Head {
		t.Errorf("versions after the restart: %+v, want the edit's %d at the head", versions, out.Version)
	}
}
