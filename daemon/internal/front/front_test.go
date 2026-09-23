package front

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"flyballd/internal/front/store"
	"flyballd/internal/principal"
)

// Merge requirement 23: a header allow-list, not a delete list; underscore
// and non-canonical spellings are dropped.
func TestHeaderAllowList(t *testing.T) {
	h := newHarness(t, Config{})
	forbidden := map[string]string{
		"Authorization":       "Bearer nope",
		"Cookie":              "flyball-8000=abc",
		"Origin":              h.srv.URL,
		"Referer":             h.srv.URL + "/",
		"X-Forwarded-Prefix":  "/evil",
		"X-Forwarded-For":     "10.9.9.9",
		"Forwarded":           "for=10.9.9.9",
		"X_Flyball_Principal": "v1.forged.forged",
		"x_flyball_scopes":    "operate:*",
		"X-Flyball-Anything":  "1",
		"X-Flyball-Scopes":    "operate:*",
		"Content_Type":        "text/evil",
		"X-Custom":            "1",
	}
	allowed := map[string]string{
		"Accept": "application/json", "Accept-Encoding": "identity", "Accept-Language": "en",
		"Cache-Control": "no-cache", "If-None-Match": `"x"`, "If-Modified-Since": "Mon, 01 Jan 2024 00:00:00 GMT",
		"User-Agent": "flyball-test", "Last-Event-Id": "7", "Mcp-Session-Id": "s1",
		"Mcp-Protocol-Version": "2025-06-18", "Traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
		"Tracestate": "k=v", "Range": "bytes=0-10", "If-Range": `"x"`,
	}
	hdr := http.Header{}
	for k, v := range forbidden {
		hdr[k] = []string{v}
	}
	for k, v := range allowed {
		hdr[k] = []string{v}
	}
	// Bearer "nope" is not a named token, so it would be a presented but
	// unclaimed credential (401); send the rest without it, then it alone.
	delete(hdr, "Authorization")
	hdr["Sec-Fetch-Site"] = []string{"same-origin"}
	resp := h.do("GET", "/api/echo", "", hdr)
	if resp.StatusCode != 200 {
		t.Fatalf("status %d: %s", resp.StatusCode, body(resp))
	}
	got := readJSON[echo](t, resp).Headers
	for k := range forbidden {
		for gk := range got {
			if strings.EqualFold(strings.ReplaceAll(gk, "_", "-"), strings.ReplaceAll(k, "_", "-")) &&
				!strings.EqualFold(gk, "X-Flyball-Principal") {
				t.Errorf("%s reached the runner as %s: %v", k, gk, got[gk])
			}
		}
	}
	if v := got.Values(principal.Header); len(v) != 1 || strings.Contains(v[0], "forged") {
		t.Errorf("principal header upstream: %v", v)
	}
	for k, v := range allowed {
		if got.Get(k) != v {
			t.Errorf("allowed %s = %q upstream, want %q", k, got.Get(k), v)
		}
	}
	if got.Get("Sec-Fetch-Site") != "" {
		t.Error("Sec-Fetch-Site is not on the allow-list but reached the runner")
	}
	if !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(got.Get("X-Request-Id")) {
		t.Errorf("X-Request-Id %q", got.Get("X-Request-Id"))
	}
	// The same for a websocket upgrade.
	ws, resp := dialWS(t, h.srv.URL, "/ws/echo", http.Header{
		"Origin": {h.srv.URL}, "X_flyball_principal": {"v1.forged.forged"}, "X-Forwarded-Prefix": {"/evil"},
		"Sec-Websocket-Protocol": {"flyball"},
	})
	if ws == nil {
		t.Fatalf("upgrade: %d %s", resp.StatusCode, body(resp))
	}
	reqs := h.runner.requests()
	last := reqs[len(reqs)-1].Headers
	for k := range last {
		if strings.Contains(strings.ToLower(k), "forward") || strings.Contains(k, "_") {
			t.Errorf("upgrade forwarded %s", k)
		}
	}
	if last.Get("Sec-Websocket-Protocol") != "flyball" || last.Get("Sec-Websocket-Key") == "" {
		t.Errorf("upgrade headers upstream: %v", last)
	}
}

// The runner verifies the principal with A1's Verify against its own key
// and aud (the fake runner refuses anything else with 401).
func TestPrincipalInjected(t *testing.T) {
	h := newHarness(t, Config{})
	resp := h.do("GET", "/api/echo?x=1", "", nil)
	if resp.StatusCode != 200 {
		t.Fatalf("status %d: %s", resp.StatusCode, body(resp))
	}
	e := readJSON[echo](t, resp)
	c := e.Claims
	if c.Sub != "local:console" || c.Nm != "local" || c.Kind != "human" || c.Aud != "run-0123abcd" ||
		!reflect.DeepEqual(c.Scp, []string{"operate", "read"}) || c.Cip != "127.0.0.1" || c.Sch != "http" || c.Sid == "" {
		t.Fatalf("claims %+v", c)
	}
	if c.Exp-c.Iat != 60 {
		t.Fatalf("lifetime %d", c.Exp-c.Iat)
	}
	if e.Host != "localhost" || e.URI != "/api/echo?x=1" {
		t.Fatalf("host %q uri %q", e.Host, e.URI)
	}
}

// Merge requirement 22: no path-based allow decisions; `..`, `%2e`, `%2f`
// and `%5c` are refused before routing.
func TestNoPathDecisions(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt})
	for _, p := range []string{
		"/api/../api/echo", "/api/%2e%2e/echo", "/api/%2E./echo", "/api/echo%2fx", "/api/echo%2Fx",
		"/api/echo%5cx", "/api/./echo", "/api/echo/..", "/api\\echo",
	} {
		if code := h.rawRequest("GET " + p + " HTTP/1.1"); code != 400 {
			t.Errorf("%s: %d, want 400", p, code)
		}
	}
	for _, r := range h.runner.requests() {
		if !strings.HasPrefix(r.Path, "/api/auth/front") {
			t.Errorf("a refused path reached the runner: %s", r.Path)
		}
	}
	// Anonymous (anonymous: none) is proxied with scp [], not refused.
	resp := h.do("GET", "/api/echo", "", nil)
	if resp.StatusCode != 200 {
		t.Fatalf("anonymous: %d", resp.StatusCode)
	}
	if c := readJSON[echo](t, resp).Claims; c.Sub != "anon:" || c.Scp == nil || len(c.Scp) != 0 {
		t.Fatalf("anonymous claims %+v", c)
	}
	// Whatever the path under /api, /ws or /mcp, the front passes it on; it
	// never answers 403 on a path.
	cookie := h.login()
	for _, p := range []string{"/api/echo/any/thing", "/api/echo/drivers/reload", "/mcp/operate", "/mcp/read"} {
		resp := h.do("POST", p, "{}", withCookie(cookie, h.origin()))
		if resp.StatusCode != 200 {
			t.Errorf("POST %s: %d, want the runner's 200", p, resp.StatusCode)
		}
	}
	resp = h.do("GET", "/api/nothing-here", "", withCookie(cookie, nil))
	if resp.StatusCode != 404 {
		t.Errorf("unknown path: %d, want the runner's 404", resp.StatusCode)
	}
}

// Merge requirement 8.
func TestHostRules(t *testing.T) {
	t.Run("local shape", func(t *testing.T) {
		h := newHarness(t, Config{})
		if code := h.rawRequest("GET /api/echo HTTP/1.1\r\nHost: evil.example"); code != 400 && code != 403 {
			t.Fatalf("two Hosts: %d", code)
		}
		for host, want := range map[string]int{"evil.example": 403, "evil.example:8000": 403, "localhost:1234": 200, "127.0.0.1": 200, "[::1]:80": 200} {
			req, _ := http.NewRequest("GET", h.srv.URL+"/api/echo", nil)
			req.Host = host
			resp, err := noRedirect.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			resp.Body.Close()
			if resp.StatusCode != want {
				t.Errorf("Host %s: %d, want %d", host, resp.StatusCode, want)
			}
		}
		// The front's own pages too.
		req, _ := http.NewRequest("GET", h.srv.URL+"/", nil)
		req.Host = "evil.example"
		resp, _ := noRedirect.Do(req)
		resp.Body.Close()
		if resp.StatusCode != 403 {
			t.Errorf("UI with a foreign Host: %d", resp.StatusCode)
		}
	})
	t.Run("url set", func(t *testing.T) {
		h := newHarness(t, Config{Listen: "127.0.0.1:8443", Auth: "password", Password: testScrypt, URL: "https://pi.lab:8443"})
		for host, want := range map[string]int{"evil.example": 403, "pi.lab": 403, "pi.lab:8443": 200, "PI.LAB:8443": 200, "localhost": 200} {
			req, _ := http.NewRequest("GET", h.srv.URL+"/api/echo", nil)
			req.Host = host
			resp, _ := noRedirect.Do(req)
			resp.Body.Close()
			if resp.StatusCode != want {
				t.Errorf("Host %s: %d, want %d", host, resp.StatusCode, want)
			}
		}
	})
	t.Run("password without url", func(t *testing.T) {
		h := newHarness(t, Config{Auth: "password", Password: testScrypt})
		for _, host := range []string{"evil.example", "pi.lab:8000", "192.168.1.20"} {
			req, _ := http.NewRequest("GET", h.srv.URL+"/api/echo", nil)
			req.Host = host
			resp, _ := noRedirect.Do(req)
			resp.Body.Close()
			if resp.StatusCode != 200 {
				t.Errorf("Host %s: %d, want 200", host, resp.StatusCode)
			}
		}
	})
}

// Merge requirement 9.
func TestOriginRules(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	cookie := h.login()
	own := h.srv.URL
	cases := []struct {
		name string
		hdr  http.Header
		want int
	}{
		{"same-site", http.Header{"Origin": {own}}, 200},
		{"foreign", http.Header{"Origin": {"https://evil.example"}}, 403},
		{"null", http.Header{"Origin": {"null"}}, 403},
		{"missing", http.Header{}, 403},
		{"missing, same-origin fetch", http.Header{"Sec-Fetch-Site": {"same-origin"}}, 200},
		{"missing, cross-site fetch", http.Header{"Sec-Fetch-Site": {"cross-site"}}, 403},
		{"two", http.Header{"Origin": {own, own}}, 403},
		{"other port", http.Header{"Origin": {"http://127.0.0.1:1"}}, 403},
	}
	for _, c := range cases {
		t.Run("cookie "+c.name, func(t *testing.T) {
			if resp := h.do("POST", "/api/echo", "{}", withCookie(cookie, c.hdr)); resp.StatusCode != c.want {
				t.Errorf("%d, want %d: %s", resp.StatusCode, c.want, body(resp))
			}
		})
	}
	// Anonymous acts are checked too (merge requirement 9 names them).
	if resp := h.do("POST", "/api/echo", "{}", http.Header{"Origin": {"https://evil.example"}}); resp.StatusCode != 403 {
		t.Errorf("anonymous foreign POST: %d", resp.StatusCode)
	}
	// GET is not an act.
	if resp := h.do("GET", "/api/echo", "", withCookie(cookie, http.Header{"Origin": {"https://evil.example"}})); resp.StatusCode != 200 {
		t.Errorf("foreign GET: %d", resp.StatusCode)
	}
	// Bearer is exempt.
	secret := h.createToken(cookie, `{"name":"ci","scopes":["operate:*","read:*"]}`)
	if resp := h.do("POST", "/api/echo", "{}", bearer(secret, http.Header{"Origin": {"https://evil.example"}})); resp.StatusCode != 200 {
		t.Errorf("bearer foreign POST: %d", resp.StatusCode)
	}
	if resp := h.do("POST", "/api/echo", "{}", bearer(secret, nil)); resp.StatusCode != 200 {
		t.Errorf("bearer without Origin: %d", resp.StatusCode)
	}
	// A websocket upgrade is checked the same way.
	if ws, resp := dialWS(t, h.srv.URL, "/ws/echo", withCookie(cookie, http.Header{"Origin": {"https://evil.example"}})); ws != nil || resp.StatusCode != 403 {
		t.Errorf("foreign upgrade was not refused")
	}
	if ws, resp := dialWS(t, h.srv.URL, "/ws/echo", withCookie(cookie, nil)); ws != nil || resp.StatusCode != 403 {
		t.Errorf("upgrade without Origin was not refused")
	}
	if ws, resp := dialWS(t, h.srv.URL, "/ws/echo", withCookie(cookie, h.origin())); ws == nil {
		t.Errorf("same-site upgrade refused: %d", resp.StatusCode)
	}
	// The login and logout routes are acts.
	if resp := h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, http.Header{"Origin": {"https://evil.example"}}); resp.StatusCode != 403 {
		t.Errorf("foreign login: %d", resp.StatusCode)
	}
	if resp := h.do("POST", "/api/auth/logout", "", withCookie(cookie, http.Header{"Origin": {"null"}})); resp.StatusCode != 403 {
		t.Errorf("null-origin logout: %d", resp.StatusCode)
	}
	// Local shape: every act is checked.
	l := newHarness(t, Config{})
	if resp := l.do("POST", "/api/echo", "{}", http.Header{"Origin": {"https://evil.example"}}); resp.StatusCode != 403 {
		t.Errorf("local foreign POST: %d", resp.StatusCode)
	}
	if resp := l.do("POST", "/api/echo", "{}", l.origin()); resp.StatusCode != 200 {
		t.Errorf("local same-site POST: %d", resp.StatusCode)
	}
	// url: adds its origin.
	u := newHarness(t, Config{Auth: "password", Password: testScrypt, URL: "https://pi.lab"})
	uc := u.login2(http.Header{"Origin": {"https://pi.lab"}, "Host": {"pi.lab"}})
	req, _ := http.NewRequest("POST", u.srv.URL+"/api/echo", strings.NewReader("{}"))
	req.Host = "pi.lab"
	req.Header.Set("Origin", "https://pi.lab")
	req.Header.Set("Cookie", uc.Name+"="+uc.Value)
	resp, _ := noRedirect.Do(req)
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("url origin POST: %d", resp.StatusCode)
	}
}

// login2 signs in with extra headers (Host is applied as the request's Host).
func (h *harness) login2(hdr http.Header) *http.Cookie {
	h.t.Helper()
	req, _ := http.NewRequest("POST", h.srv.URL+"/api/auth/login", strings.NewReader(`{"password":"`+testPassword+`"}`))
	for k, v := range hdr {
		if k == "Host" {
			req.Host = v[0]
			continue
		}
		req.Header[k] = v
	}
	resp, err := noRedirect.Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		h.t.Fatalf("login: %d %s", resp.StatusCode, body(resp))
	}
	return resp.Cookies()[0]
}

// createToken makes a named token through the route and returns its secret.
func (h *harness) createToken(cookie *http.Cookie, req string) string {
	h.t.Helper()
	resp := h.do("POST", "/api/auth/tokens", req, withCookie(cookie, h.origin()))
	if resp.StatusCode != 201 {
		h.t.Fatalf("create token: %d %s", resp.StatusCode, body(resp))
	}
	return readJSON[struct {
		Token string `json:"token"`
	}](h.t, resp).Token
}

// Merge requirement 14 (the front's half): the limit is per real peer,
// X-Forwarded-For counts only from trusted_proxies, a full hashing
// semaphore answers 429, and the error strings are fixed.
func TestLoginLimits(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt})
	wrong := `{"password":"nope"}`
	for i := 0; i < store.LoginAttempts; i++ {
		resp := h.do("POST", "/api/auth/login", wrong, h.origin())
		if resp.StatusCode != 401 || body(resp) != `{"detail":"Wrong password"}` {
			t.Fatalf("attempt %d: %d", i, resp.StatusCode)
		}
	}
	resp := h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, h.origin())
	if resp.StatusCode != 429 || resp.Header.Get("Retry-After") == "" || body(resp) != `{"detail":"Too many attempts; try again later"}` {
		t.Fatalf("held-off peer: %d %q", resp.StatusCode, resp.Header.Get("Retry-After"))
	}
	// A forged X-Forwarded-For does not make a fresh peer.
	resp = h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, http.Header{"Origin": {h.srv.URL}, "X-Forwarded-For": {"203.0.113.9"}})
	if resp.StatusCode != 429 {
		t.Fatalf("X-Forwarded-For from an untrusted peer counted: %d", resp.StatusCode)
	}

	// With the peer trusted, X-Forwarded-For names the client.
	tr := newHarness(t, Config{Auth: "password", Password: testScrypt, TrustedProxies: []string{"127.0.0.1"}})
	xff := func(ip string) http.Header { return http.Header{"Origin": {tr.srv.URL}, "X-Forwarded-For": {ip}} }
	for i := 0; i < store.LoginAttempts; i++ {
		tr.do("POST", "/api/auth/login", wrong, xff("203.0.113.9"))
	}
	if resp := tr.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, xff("203.0.113.9")); resp.StatusCode != 429 {
		t.Fatalf("trusted XFF peer not held off: %d", resp.StatusCode)
	}
	if resp := tr.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, xff("203.0.113.10")); resp.StatusCode != 200 {
		t.Fatalf("another client behind the trusted proxy: %d", resp.StatusCode)
	}
	if c := readJSON[echo](t, tr.do("GET", "/api/echo", "", xff("203.0.113.10"))).Claims; c.Cip != "203.0.113.10" {
		t.Fatalf("cip behind a trusted proxy: %q", c.Cip)
	}
	if c := readJSON[echo](t, h.do("GET", "/api/echo", "", http.Header{"X-Forwarded-For": {"203.0.113.10"}})).Claims; c.Cip != "127.0.0.1" {
		t.Fatalf("cip from an untrusted XFF: %q", c.Cip)
	}

	// The hashing semaphore: a slow line (n=2^15, r=8: 32 MiB) keeps both
	// slots busy; a third check is refused at once.
	slow := newHarness(t, Config{Auth: "password", Password: scryptLine(testPassword, 1<<15, 8)})
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < store.HashingSlots; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			req, _ := http.NewRequest("POST", slow.srv.URL+"/api/auth/login", strings.NewReader(`{"password":"x"}`))
			req.Header.Set("Origin", slow.srv.URL)
			if resp, err := noRedirect.Do(req); err == nil {
				resp.Body.Close()
			}
		}()
	}
	close(start)
	time.Sleep(100 * time.Millisecond)
	resp = slow.do("POST", "/api/auth/login", `{"password":"x"}`, slow.origin())
	wg.Wait()
	if resp.StatusCode != 429 || resp.Header.Get("Retry-After") == "" || body(resp) != `{"detail":"Too many attempts; try again later"}` {
		t.Fatalf("third concurrent check: %d", resp.StatusCode)
	}
}

// Merge requirement 11: logout, token revocation (by the route and by
// another process), token expiry and session expiry close the affected
// open websocket and stream within a second; a short in-flight POST
// completes.
func TestRevocationClosesSockets(t *testing.T) {
	t.Run("logout", func(t *testing.T) {
		h := newHarness(t, Config{Auth: "password", Password: testScrypt})
		cookie := h.login()
		other := h.login() // a second session: its socket stays open
		ws := h.openWS(withCookie(cookie, h.origin()))
		keep := h.openWS(withCookie(other, h.origin()))
		stream := h.openStream(withCookie(cookie, nil))
		slow := h.startSlowPOST(withCookie(cookie, h.origin()))
		time.Sleep(100 * time.Millisecond)

		at := time.Now()
		if resp := h.do("POST", "/api/auth/logout", "", withCookie(cookie, h.origin())); resp.StatusCode != 200 {
			t.Fatalf("logout: %d", resp.StatusCode)
		}
		h.expectClosed(ws, stream, at)
		if code, _ := keep.closeCode(300 * time.Millisecond); code != -2 {
			t.Fatalf("another session's socket closed too: %d", code)
		}
		if got := <-slow; got != "200 done" {
			t.Fatalf("the in-flight POST: %s", got)
		}
		// The cookie no longer works.
		if resp := h.do("GET", "/api/echo", "", withCookie(cookie, nil)); resp.StatusCode != 401 {
			t.Fatalf("after logout: %d", resp.StatusCode)
		}
	})
	t.Run("token revoked by the route", func(t *testing.T) {
		h := newHarness(t, Config{Auth: "password", Password: testScrypt})
		admin := h.login()
		secret := h.createToken(admin, `{"name":"watcher","scopes":["read:*","operate:*"]}`)
		id := h.tokenID("watcher", admin)
		ws := h.openWS(bearer(secret, nil))
		stream := h.openStream(bearer(secret, nil))
		slow := h.startSlowPOST(bearer(secret, nil))
		time.Sleep(100 * time.Millisecond)
		at := time.Now()
		if resp := h.do("DELETE", "/api/auth/tokens/"+id, "", withCookie(admin, h.origin())); resp.StatusCode != 204 {
			t.Fatalf("revoke: %d %s", resp.StatusCode, body(resp))
		}
		h.expectClosed(ws, stream, at)
		if got := <-slow; got != "200 done" {
			t.Fatalf("the in-flight POST: %s", got)
		}
		if resp := h.do("GET", "/api/echo", "", bearer(secret, nil)); resp.StatusCode != 401 {
			t.Fatalf("revoked token: %d", resp.StatusCode)
		}
	})
	t.Run("token revoked by the CLI", func(t *testing.T) {
		h := newHarness(t, Config{})
		cli, err := store.OpenTokens(h.opts.TokensPath, store.TokensOptions{})
		if err != nil {
			t.Fatal(err)
		}
		secret, tok, err := cli.Create(store.NewToken{Name: "ci", Scopes: []string{"read:*"}})
		if err != nil {
			t.Fatal(err)
		}
		ws := h.openWS(bearer(secret, nil))
		stream := h.openStream(bearer(secret, nil))
		time.Sleep(100 * time.Millisecond)
		at := time.Now()
		if err := cli.Revoke(tok.ID); err != nil {
			t.Fatal(err)
		}
		h.expectClosed(ws, stream, at)
	})
	t.Run("session expiry", func(t *testing.T) {
		h := newHarness(t, Config{Auth: "password", Password: testScrypt, Session: "1500ms"})
		cookie := h.login()
		ws := h.openWS(withCookie(cookie, h.origin()))
		stream := h.openStream(withCookie(cookie, nil))
		at := time.Now().Add(1500 * time.Millisecond)
		h.expectClosed(ws, stream, at)
	})
	t.Run("token expiry", func(t *testing.T) {
		clock := &fakeClock{now: time.Now()}
		h := newHarness(t, Config{}, func(o *Options) { o.TokensNow = clock.Now })
		cli, _ := store.OpenTokens(h.opts.TokensPath, store.TokensOptions{Now: clock.Now})
		secret, _, err := cli.Create(store.NewToken{Name: "short", Scopes: []string{"read:*"}, ExpiresIn: time.Hour})
		if err != nil {
			t.Fatal(err)
		}
		ws := h.openWS(bearer(secret, nil))
		stream := h.openStream(bearer(secret, nil))
		time.Sleep(100 * time.Millisecond)
		at := time.Now()
		clock.Add(2 * time.Hour)
		h.expectClosed(ws, stream, at)
	})
}

type fakeClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *fakeClock) Add(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
}

func (h *harness) openWS(hdr http.Header) *wsClient {
	h.t.Helper()
	ws, resp := dialWS(h.t, h.srv.URL, "/ws/echo", hdr)
	if ws == nil {
		h.t.Fatalf("upgrade: %d %s", resp.StatusCode, body(resp))
	}
	if op, payload, err := ws.next(2 * time.Second); err != nil || op != 1 || string(payload) != "hello" {
		h.t.Fatalf("first frame: %d %q %v", op, payload, err)
	}
	return ws
}

// openStream starts GET /api/stream and returns a channel that gets the
// time the body ended.
func (h *harness) openStream(hdr http.Header) chan time.Time {
	h.t.Helper()
	req, _ := http.NewRequest("GET", h.srv.URL+"/api/stream", nil)
	for k, v := range hdr {
		req.Header[k] = v
	}
	resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	if resp.StatusCode != 200 {
		h.t.Fatalf("stream: %d", resp.StatusCode)
	}
	ended := make(chan time.Time, 1)
	go func() {
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		ended <- time.Now()
	}()
	return ended
}

// startSlowPOST sends POST /api/slow (1.5 s at the runner) and returns a
// channel that gets "<status> <body>".
func (h *harness) startSlowPOST(hdr http.Header) chan string {
	out := make(chan string, 1)
	go func() {
		req, _ := http.NewRequest("POST", h.srv.URL+"/api/slow", strings.NewReader("{}"))
		for k, v := range hdr {
			req.Header[k] = v
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
		if err != nil {
			out <- err.Error()
			return
		}
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		out <- fmt.Sprintf("%d %s", resp.StatusCode, b)
	}()
	return out
}

// expectClosed: the websocket gets close 4401 and the stream ends, each
// within a second of at; the runner sees both ends.
func (h *harness) expectClosed(ws *wsClient, stream chan time.Time, at time.Time) {
	h.t.Helper()
	code, reason := ws.closeCode(time.Until(at.Add(3 * time.Second)))
	closedAt := time.Now()
	if code != 4401 {
		h.t.Fatalf("websocket close %d %q, want 4401", code, reason)
	}
	if d := closedAt.Sub(at); d > time.Second {
		h.t.Fatalf("websocket closed %v after revocation", d)
	}
	select {
	case end := <-stream:
		if d := end.Sub(at); d > time.Second {
			h.t.Fatalf("stream ended %v after revocation", d)
		}
	case <-time.After(time.Until(at.Add(3 * time.Second))):
		h.t.Fatal("the stream is still open")
	}
	for _, ch := range []chan struct{}{h.runner.wsEnded, h.runner.streamed} {
		select {
		case <-ch:
		case <-time.After(2 * time.Second):
			h.t.Fatal("the runner's side stayed open")
		}
	}
}

func (h *harness) tokenID(name string, admin *http.Cookie) string {
	h.t.Helper()
	rows := readJSON[[]store.Token](h.t, h.do("GET", "/api/auth/tokens", "", withCookie(admin, nil)))
	for _, r := range rows {
		if r.Name == name {
			return r.ID
		}
	}
	h.t.Fatalf("no token %q", name)
	return ""
}

// Merge requirement 26: a refused upgrade completes the handshake and
// closes 4401 -- not 403, not 1006.
func TestRefusedUpgrade4401(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt})
	check := func(name string, hdr http.Header) {
		t.Helper()
		ws, resp := dialWS(t, h.srv.URL, "/ws/echo", hdr)
		if ws == nil {
			t.Fatalf("%s: HTTP %d, want a completed handshake", name, resp.StatusCode)
		}
		if code, _ := ws.closeCode(2 * time.Second); code != 4401 {
			t.Fatalf("%s: close %d, want 4401", name, code)
		}
	}
	// A stale cookie (the front's refusal).
	check("stale cookie", withCookie(&http.Cookie{Name: "flyball-8000", Value: "stale"}, h.origin()))
	// A revoked or unknown token.
	check("unknown token", bearer(store.TokenPrefix+strings.Repeat("A", 43), nil))
	// Anonymous, which the runner refuses 403: the front makes it 4401.
	check("anonymous", h.origin())
	// The runner's own 4401 (its principal check failed) means front and
	// runner are out of step: the client sees 1014, not a sign-out.
	cookie := h.login()
	ws, resp := dialWS(t, h.srv.URL, "/ws/kick", withCookie(cookie, h.origin()))
	if ws == nil {
		t.Fatalf("kick: %d", resp.StatusCode)
	}
	if code, _ := ws.closeCode(2 * time.Second); code != 1014 {
		t.Fatalf("runner 4401: close %d, want 1014", code)
	}
	// A websocket through the front works both ways, and Set-Cookie on the
	// runner's 101 is dropped.
	ws, resp = dialWS(t, h.srv.URL, "/ws/echo", withCookie(cookie, h.origin()))
	if resp.Header.Get("Set-Cookie") != "" {
		t.Fatal("the runner's Set-Cookie reached the client on a 101")
	}
	if op, p, err := ws.next(2 * time.Second); err != nil || op != 1 || string(p) != "hello" {
		t.Fatalf("hello: %v %q", err, p)
	}
	writeClientFrame(ws.conn, 1, []byte("ping"))
	if _, p, err := ws.next(2 * time.Second); err != nil || string(p) != "ping" {
		t.Fatalf("echo: %v %q", err, p)
	}
	writeClientFrame(ws.conn, 8, closePayload(1000, "bye"))
	if code, _ := ws.closeCode(2 * time.Second); code != 1000 {
		t.Fatalf("clean close: %d", code)
	}
}

// F18: the runner's 401 means front and runner are out of step: 502, and
// the UI is not signed out.
func TestRunner401Becomes502(t *testing.T) {
	h := newHarness(t, Config{})
	resp := h.do("GET", "/api/bad", "", nil)
	if resp.StatusCode != 502 || !strings.Contains(body(resp), "out of step") {
		t.Fatalf("%d", resp.StatusCode)
	}
}

func TestAnonymous403Becomes401(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	if resp := h.do("GET", "/api/guarded", "", nil); resp.StatusCode != 200 {
		t.Fatalf("anonymous read: %d", resp.StatusCode)
	}
	resp := h.do("POST", "/api/guarded", "{}", h.origin())
	if resp.StatusCode != 401 {
		t.Fatalf("anonymous act: %d, want 401", resp.StatusCode)
	}
	if !strings.Contains(body(resp), `"needed":"operate"`) {
		t.Fatal("the runner's body was not kept")
	}
	// A signed-in caller lacking the verb keeps the 403.
	cookie := h.login()
	secret := h.createToken(cookie, `{"name":"ro","scopes":["read:*"]}`)
	if resp := h.do("POST", "/api/guarded", "{}", bearer(secret, nil)); resp.StatusCode != 403 {
		t.Fatalf("read token acting: %d, want 403", resp.StatusCode)
	}
}

// §WP0-4: `__Host-flyball` under TLS or an https url:, `flyball-<port>`
// otherwise; HttpOnly, SameSite=Lax, Path=/, Secure with TLS.
func TestCookieNames(t *testing.T) {
	check := func(t *testing.T, raw, name string, secure bool) {
		t.Helper()
		if !strings.HasPrefix(raw, name+"=") {
			t.Fatalf("cookie %q, want %s", raw, name)
		}
		for _, attr := range []string{"Path=/", "HttpOnly", "SameSite=Lax"} {
			if !strings.Contains(raw, attr) {
				t.Fatalf("cookie %q lacks %s", raw, attr)
			}
		}
		if strings.Contains(raw, "Secure") != secure || strings.Contains(raw, "Domain") {
			t.Fatalf("cookie %q: Secure want %v, no Domain", raw, secure)
		}
	}
	t.Run("plain", func(t *testing.T) {
		h := newHarness(t, Config{Listen: "127.0.0.1:8123", Auth: "password", Password: testScrypt})
		resp := h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, h.origin())
		check(t, resp.Header.Get("Set-Cookie"), "flyball-8123", false)
	})
	t.Run("https url", func(t *testing.T) {
		h := newHarness(t, Config{Listen: "127.0.0.1:8123", Auth: "password", Password: testScrypt, URL: "https://pi.lab"})
		c := &http.Client{}
		req, _ := http.NewRequest("POST", h.srv.URL+"/api/auth/login", strings.NewReader(`{"password":"`+testPassword+`"}`))
		req.Host = "pi.lab"
		req.Header.Set("Origin", "https://pi.lab")
		resp, err := c.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		check(t, resp.Header.Get("Set-Cookie"), "__Host-flyball", true)
	})
	t.Run("TLS", func(t *testing.T) {
		dir := t.TempDir()
		cert, key := filepath.Join(dir, "c.pem"), filepath.Join(dir, "k.pem")
		writeCert(t, cert, key)
		fr := newFakeRunner(t, "tls-rig")
		plan := Resolve(Config{Listen: "127.0.0.1:8443", Auth: "password", Password: testScrypt, TLS: &TLSFiles{Cert: cert, Key: key}}, false)
		defer plan.Close()
		if plan.TLS == nil {
			t.Fatalf("no TLS: %s", plan.Fallback)
		}
		f := New(Options{Plan: plan, Route: SingleRig(Rig{Name: "r", Target: func(context.Context) (Target, error) { return fr.target(), nil }})})
		defer f.Close()
		srv := NewServer(plan, f)
		ln, err := Listen(Plan{Listen: "127.0.0.1:0", TLS: plan.TLS})
		if err != nil {
			t.Fatal(err)
		}
		go srv.Serve(ln)
		defer srv.Close()
		base := "https://" + ln.Addr().String()
		c := &http.Client{Transport: &http.Transport{TLSClientConfig: insecureTLS()}}
		req, _ := http.NewRequest("POST", base+"/api/auth/login", strings.NewReader(`{"password":"`+testPassword+`"}`))
		req.Header.Set("Origin", base)
		resp, err := c.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			t.Fatalf("TLS login: %d %s", resp.StatusCode, body(resp))
		}
		check(t, resp.Header.Get("Set-Cookie"), "__Host-flyball", true)
		// The principal says https.
		req, _ = http.NewRequest("GET", base+"/api/echo", nil)
		req.Header.Set("Cookie", strings.Split(resp.Header.Get("Set-Cookie"), ";")[0])
		r2, err := c.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		if cl := readJSON[echo](t, r2).Claims; cl.Sch != "https" || cl.Sub != "local:admin" {
			t.Fatalf("claims over TLS: %+v", cl)
		}
	})
}

// Bound recomputes the cookie name from the port actually bound: a plan
// asking for port 0 (`--listen 127.0.0.1:0`) sees "0" until the listener
// says otherwise, so two such fronts would otherwise share "flyball-0".
func TestBoundRenamesCookie(t *testing.T) {
	h := newHarness(t, Config{Listen: "127.0.0.1:0", Auth: "password", Password: testScrypt})
	h.front.Bound(&net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 51234})
	resp := h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, h.origin())
	if raw := resp.Header.Get("Set-Cookie"); !strings.HasPrefix(raw, "flyball-51234=") {
		t.Fatalf("cookie %q, want flyball-51234", raw)
	}
}

// Merge requirement 24: every misconfiguration serves local on loopback
// with a banner, and the runner still receives requests. (A credential
// shape's console is on a fresh port: TestFallbackRefusesRequestedListen.)
func TestFallbacks(t *testing.T) {
	dir := t.TempDir()
	for name, c := range map[string]Config{
		"plaintext password": {Listen: "127.0.0.1:0", Auth: "password", Password: "change-me"},
		"sso":                {Listen: "127.0.0.1:0", Auth: "sso"},
		"bad TLS files": {Listen: "127.0.0.1:0", Auth: "password", Password: testScrypt,
			TLS: &TLSFiles{Cert: filepath.Join(dir, "x.pem"), Key: filepath.Join(dir, "x.key")}},
		"unknown shape":      {Listen: "127.0.0.1:0", Auth: "kerberos"},
		"non-loopback local": {Listen: "0.0.0.0:9000"},
	} {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t, c)
			want := "127.0.0.1:0"
			if c.Auth == "" {
				want = "127.0.0.1:9000"
			}
			if h.plan.Shape != "local" || h.plan.Listen != want || h.plan.Fallback == "" {
				t.Fatalf("plan %+v", h.plan)
			}
			resp := h.do("POST", "/api/echo", "{}", h.origin())
			if resp.StatusCode != 200 {
				t.Fatalf("the runner did not receive the request: %d", resp.StatusCode)
			}
			if c := readJSON[echo](t, resp).Claims; c.Sub != "local:console" {
				t.Fatalf("claims %+v", c)
			}
			info := readJSON[AuthInfo](t, h.do("GET", "/api/auth", "", nil))
			if info.Shape != "local" || info.Exposure == nil || !info.Exposure.Restricted ||
				info.Exposure.Warning == nil || !strings.Contains(*info.Exposure.Warning, h.plan.Fallback) {
				t.Fatalf("/api/auth: %+v %+v", info, info.Exposure)
			}
		})
	}
}

// D-028, amended (sec F1): a credential shape that falls back must not serve
// the local shape where it was asked to listen -- a reverse proxy on the
// same host still forwards there, and the local shape gives every caller
// every verb. The requested address answers 503 with the reason; the local
// console is on a fresh loopback address (a fresh socket beside a unix one).
func TestFallbackRefusesRequestedListen(t *testing.T) {
	dir := t.TempDir()
	sockDir, err := os.MkdirTemp("", "fb-front-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(sockDir) })
	os.Chmod(sockDir, 0o700)
	refused := func(*ProxyConfig, Plan) (Client, error) {
		return nil, errors.New("secret_file /etc/flyball/secret is readable by every user; chmod o-r it")
	}
	for _, c := range []struct {
		name   string
		cfg    Config
		reason string
	}{
		{"proxy preset refused", Config{Auth: "proxy", Proxy: &ProxyConfig{Preset: "authelia"}}, "chmod o-r"},
		{"proxy preset refused, unix", Config{Auth: "proxy", Listen: "unix:" + filepath.Join(sockDir, "front.sock"),
			Proxy: &ProxyConfig{Preset: "authelia"}}, "chmod o-r"},
		{"password, bad TLS", Config{Auth: "password", Password: testScrypt,
			TLS: &TLSFiles{Cert: filepath.Join(dir, "x.pem"), Key: filepath.Join(dir, "x.key")}}, "tls:"},
		{"plaintext password", Config{Auth: "password", Password: "hunter2"}, "plaintext passwords are refused"},
	} {
		t.Run(c.name, func(t *testing.T) {
			if c.cfg.Listen == "" {
				c.cfg.Listen = freeLoopback(t)
			}
			requested := c.cfg.Listen
			plan, client := ResolveWith(c.cfg, false, refused)
			t.Cleanup(plan.Close)
			fr := newFakeRunner(t, "run-0123abcd")
			f := New(Options{Plan: plan, Proxy: client,
				Route: SingleRig(Rig{Name: "blender", Target: func(context.Context) (Target, error) { return fr.target(), nil }})})
			t.Cleanup(f.Close)
			// Serve the plan as frontwire.Serve does.
			ln, err := Listen(plan)
			if err != nil {
				t.Fatalf("the console cannot listen on %s: %v", plan.Listen, err)
			}
			f.Bound(ln.Addr())
			srv := NewServer(plan, f)
			go srv.Serve(ln)
			t.Cleanup(func() { srv.Close() })
			console := ln.Addr().String()
			if ln.Addr().Network() == "unix" {
				console = "unix:" + console
			}

			// A proxied request to the address the proxy points at: Host
			// rewritten to the upstream, a same-site Origin, identity headers.
			upstream := strings.TrimPrefix(requested, "unix:")
			hdr := http.Header{"Origin": {"http://" + hostOf(requested)}, "Remote-User": {"mallory"},
				"X-Forwarded-For": {"203.0.113.9"}, "Content-Type": {"application/json"}}
			resp := sendTo(t, requested, "POST", "/api/echo", hdr)
			b := body(resp)
			if resp.StatusCode != 503 || !strings.Contains(b, "misconfigured") || !strings.Contains(b, c.reason) {
				t.Fatalf("the requested listen %s answered %d %q, want 503 naming %q", upstream, resp.StatusCode, b, c.reason)
			}
			if strings.Contains(b, "hunter2") {
				t.Fatalf("the refusal leaks the password: %q", b)
			}
			if n := len(fr.requests()); n != 0 {
				t.Fatalf("the runner received %d requests through the refused address", n)
			}

			// The console is elsewhere, on loopback, and is the local shape.
			if console == requested {
				t.Fatalf("the local console is on the requested address %s", requested)
			}
			if !loopbackListen(console) {
				t.Fatalf("the console %s is not loopback", console)
			}
			resp = sendTo(t, console, "POST", "/api/echo", http.Header{"Origin": {"http://" + hostOf(console)}, "Content-Type": {"application/json"}})
			if resp.StatusCode != 200 {
				t.Fatalf("console: %d %s", resp.StatusCode, body(resp))
			}
			if cl := readJSON[echo](t, resp).Claims; cl.Sub != "local:console" {
				t.Fatalf("console claims %+v", cl)
			}
			info := readJSON[AuthInfo](t, sendTo(t, console, "GET", "/api/auth", nil))
			e := info.Exposure
			if info.Shape != "local" || e == nil || !e.Restricted || e.Requested != requested || e.Warning == nil ||
				!strings.Contains(*e.Warning, c.reason) || !strings.Contains(*e.Warning, "503") {
				t.Fatalf("/api/auth: %+v %+v", info, e)
			}
			if !strings.HasPrefix(console, "unix:") {
				if _, port, _ := net.SplitHostPort(console); fmt.Sprint(e.Port) != port {
					t.Fatalf("exposure port %d, want the console's %s", e.Port, port)
				}
			}
		})
	}
}

// freeLoopback is a 127.0.0.1 address nobody listens on (just now).
func freeLoopback(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	ln.Close()
	return addr
}

// hostOf is the Host a client sends to listen (a unix socket: localhost).
func hostOf(listen string) string {
	if strings.HasPrefix(listen, "unix:") {
		return "localhost"
	}
	return listen
}

// sendTo sends one request to a listen address, TCP or unix:/path.
func sendTo(t *testing.T, listen, method, path string, hdr http.Header) *http.Response {
	t.Helper()
	client := &http.Client{Timeout: 10 * time.Second}
	if sock, ok := strings.CutPrefix(listen, "unix:"); ok {
		client.Transport = &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", sock)
		}}
	}
	var rd io.Reader
	if method == "POST" {
		rd = strings.NewReader("{}")
	}
	req, err := http.NewRequest(method, "http://"+hostOf(listen)+path, rd)
	if err != nil {
		t.Fatal(err)
	}
	for k, v := range hdr {
		req.Header[k] = v
	}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("%s %s on %s: %v", method, path, listen, err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

// Merge requirement 32.
func TestPlaceholderUI(t *testing.T) {
	h := newHarness(t, Config{}, func(o *Options) {
		o.UI = placeholderFS()
	})
	for _, p := range []string{"/", "/index.html", "/some/route"} {
		resp := h.do("GET", p, "", nil)
		b := body(resp)
		if resp.StatusCode != 503 || !strings.Contains(b, "build-with-ui.sh") || !strings.Contains(resp.Header.Get("Content-Type"), "text/html") {
			t.Fatalf("%s: %d %q", p, resp.StatusCode, b)
		}
	}
	// The API still works.
	if resp := h.do("GET", "/api/echo", "", nil); resp.StatusCode != 200 {
		t.Fatalf("api: %d", resp.StatusCode)
	}
	// A real build is served, with the front-page headers.
	r := newHarness(t, Config{})
	resp := r.do("GET", "/", "", nil)
	if resp.StatusCode != 200 || body(resp) != testUI {
		t.Fatalf("UI: %d", resp.StatusCode)
	}
	if resp.Header.Get("Content-Security-Policy") != "frame-ancestors 'none'" || resp.Header.Get("X-Frame-Options") != "DENY" ||
		resp.Header.Get("X-Content-Type-Options") != "nosniff" {
		t.Fatalf("front page headers: %v", resp.Header)
	}
	if resp := r.do("POST", "/", "{}", r.origin()); resp.StatusCode != 405 {
		t.Fatalf("POST to the UI: %d", resp.StatusCode)
	}
}

func placeholderFS() fstestFS {
	return fstestFS{".placeholder": {Data: []byte("This flyball binary was built with a plain `go build`")}}
}

// §WP0-2's examples, byte-compared after decoding.
func TestAuthInfoV2(t *testing.T) {
	decode := func(s string) any {
		var v any
		if err := json.Unmarshal([]byte(s), &v); err != nil {
			t.Fatal(err)
		}
		return v
	}
	get := func(h *harness, hdr http.Header) any {
		resp := h.do("GET", "/api/auth", "", hdr)
		if resp.StatusCode != 200 || resp.Header.Get("Cache-Control") != "no-store" {
			t.Fatalf("GET /api/auth: %d %v", resp.StatusCode, resp.Header)
		}
		return decode(body(resp))
	}
	local := newHarness(t, Config{})
	want := decode(`{"v":2,"shape":"local","scheme":"local","user":{"id":"local:console","name":"local","kind":"human"},"verbs":["operate","read"],"anonymous":"none","login":{"password":false,"token":false,"passkey":false,"sso":null},"exposure":null,"rig":"blender"}`)
	if got := get(local, nil); !reflect.DeepEqual(got, want) {
		t.Errorf("local:\n got %v\nwant %v", got, want)
	}

	pw := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read", Listen: "0.0.0.0:8000"}, func(o *Options) {})
	// Listening beyond loopback without TLS: the exposure carries the
	// cleartext warning; compare the rest exactly.
	got := get(pw, nil).(map[string]any)
	exp := got["exposure"]
	delete(got, "exposure")
	want = decode(`{"v":2,"shape":"password","scheme":"anonymous","user":null,"verbs":["read"],"anonymous":"read","login":{"password":true,"token":false,"passkey":false,"sso":null},"rig":"blender"}`)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("password, anonymous:\n got %v\nwant %v", got, want)
	}
	if e, ok := exp.(map[string]any); !ok || e["requested"] != "0.0.0.0:8000" || e["open"] != false || !strings.Contains(fmt.Sprint(e["warning"]), "plain HTTP") {
		t.Errorf("password exposure: %v", exp)
	}

	cookie := pw.login()
	g := get(pw, withCookie(cookie, nil)).(map[string]any)
	if g["scheme"] != "session" || !reflect.DeepEqual(g["user"], decode(`{"id":"local:admin","name":"admin","kind":"human"}`)) ||
		!reflect.DeepEqual(g["verbs"], decode(`["operate","read"]`)) {
		t.Errorf("signed in: %v", g)
	}

	// The bare runner's example is the runner's to answer; the Go type
	// encodes it exactly.
	bareJSON := `{"v":2,"shape":"bare","scheme":"anonymous","user":null,"verbs":[],"anonymous":"none","login":{"password":false,"token":true,"passkey":false,"sso":null},"exposure":null}`
	b, _ := json.Marshal(AuthInfo{V: 2, Shape: "bare", Scheme: "anonymous", Verbs: []string{}, Anonymous: "none", Login: AuthLogin{Token: true}})
	if string(b) != bareJSON {
		t.Errorf("bare:\n got %s\nwant %s", b, bareJSON)
	}

	// A stale cookie on GET /api/auth is anonymous, and the cookie is cleared.
	resp := pw.do("GET", "/api/auth", "", withCookie(&http.Cookie{Name: cookie.Name, Value: "stale"}, nil))
	if resp.StatusCode != 200 || !strings.Contains(resp.Header.Get("Set-Cookie"), "Max-Age=0") {
		t.Fatalf("stale cookie: %d %q", resp.StatusCode, resp.Header.Get("Set-Cookie"))
	}
	if info := readJSON[AuthInfo](t, resp); info.Scheme != "anonymous" {
		t.Fatalf("stale cookie: %+v", info)
	}
	// ... but on a guarded route it is refused.
	resp = pw.do("GET", "/api/echo", "", withCookie(&http.Cookie{Name: cookie.Name, Value: "stale"}, nil))
	if resp.StatusCode != 401 || !strings.Contains(resp.Header.Get("Set-Cookie"), "Max-Age=0") {
		t.Fatalf("stale cookie on a guarded route: %d", resp.StatusCode)
	}
	// Logout answers the anonymous view.
	resp = pw.do("POST", "/api/auth/logout", "", withCookie(cookie, pw.origin()))
	if info := readJSON[AuthInfo](t, resp); resp.StatusCode != 200 || info.Scheme != "anonymous" || info.User != nil {
		t.Fatalf("logout: %d %+v", resp.StatusCode, info)
	}
	// Passkeys are reserved.
	if resp := pw.do("GET", "/api/auth/passkey/challenge", "", nil); resp.StatusCode != 404 {
		t.Fatalf("passkey: %d", resp.StatusCode)
	}
}

// adv-blind 13: the server limits exist (they did in serve_ui.go).
func TestTimeouts(t *testing.T) {
	srv := NewServer(Plan{Listen: "127.0.0.1:0"}, http.NotFoundHandler())
	if srv.ReadHeaderTimeout != 10*time.Second || srv.IdleTimeout != 120*time.Second || srv.MaxHeaderBytes != 64<<10 {
		t.Fatalf("server limits: %v %v %v", srv.ReadHeaderTimeout, srv.IdleTimeout, srv.MaxHeaderBytes)
	}
	if srv.ReadTimeout != 0 || srv.WriteTimeout != 0 {
		t.Fatal("a Read/WriteTimeout would cut websockets and long streams")
	}
	// Real: a slow client is dropped after ReadHeaderTimeout.
	srv.ReadHeaderTimeout = 200 * time.Millisecond
	ln, err := Listen(Plan{Listen: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	go srv.Serve(ln)
	defer srv.Close()
	conn, err := netDial(ln.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.Write([]byte("GET / HTTP/1.1\r\nHost: x\r\n"))
	conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	start := time.Now()
	buf := make([]byte, 512)
	for {
		if _, err := conn.Read(buf); err != nil {
			break
		}
	}
	if d := time.Since(start); d > 1500*time.Millisecond {
		t.Fatalf("a slow client held the connection %v", d)
	}
}

// Merge requirement 17 (the audit's half) and adv-blind 6.
func TestAuditJSONL(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state", "front", "audit.jsonl")
	a, err := OpenAudit(path)
	if err != nil {
		t.Fatal(err)
	}
	h := newHarness(t, Config{Auth: "password", Password: testScrypt}, func(o *Options) { o.Audit = a })
	cookie := h.login()
	h.do("POST", "/api/auth/login", `{"password":"wrong"}`, h.origin())
	evil := "ci\n{\"event\":\"login.ok\"}\x1b[31m"
	evilJSON, _ := json.Marshal(map[string]any{"name": evil, "scopes": []string{"read"}})
	// The store refuses control characters in a name; the refused attempt
	// is audited with the name as given, escaped.
	if resp := h.do("POST", "/api/auth/tokens", string(evilJSON), withCookie(cookie, h.origin())); resp.StatusCode != 400 {
		t.Fatalf("a control-character name: %d", resp.StatusCode)
	}
	secret := h.createToken(cookie, `{"name":"ci","scopes":["read"]}`)
	h.do("DELETE", "/api/auth/tokens/"+h.tokenID("ci", cookie), "", withCookie(cookie, h.origin()))
	h.do("GET", "/api/echo", "", bearer(secret, nil))
	h.do("POST", "/api/auth/logout", "", withCookie(cookie, h.origin()))
	a.Close()

	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("audit mode %v", info.Mode())
	}
	raw, _ := os.ReadFile(path)
	if strings.ContainsRune(string(raw), 0x1b) || strings.Contains(string(raw), secret) {
		t.Fatal("the audit holds a raw escape or a token secret")
	}
	var events []string
	var seq []float64
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var rec map[string]any
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			t.Fatalf("line %q: %v", line, err)
		}
		events = append(events, rec["event"].(string))
		seq = append(seq, rec["seq"].(float64))
		if rec["boot"] == nil || rec["time"] == nil {
			t.Fatalf("record %v lacks boot or time", rec)
		}
	}
	for _, want := range []string{"login.ok", "login.fail", "token.create.refused", "token.refused", "token.create", "token.revoke", "logout"} {
		if !slices.Contains(events, want) {
			t.Errorf("no %s event in %v", want, events)
		}
	}
	if !slices.IsSorted(seq) {
		t.Errorf("seq not increasing: %v", seq)
	}
	if strings.Count(string(raw), "\n") != len(events) {
		t.Error("a name broke a record over two lines")
	}
	if !strings.Contains(string(raw), `ci\n{\"event\":\"login.ok\"}\u001b[31m`) {
		t.Errorf("the refused name is not in the audit, escaped: %s", raw)
	}
}

// Merge requirement 17: no credential in a URL.
func TestNoCredentialInURL(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt})
	cookie := h.login()
	secret := h.createToken(cookie, `{"name":"ci","scopes":["operate:*","read:*"]}`)
	for _, q := range []string{"?token=", "?access_token=", "?flyball_token="} {
		resp := h.do("GET", "/api/echo"+q+secret, "", nil)
		if c := readJSON[echo](t, resp).Claims; c.Sub != "anon:" || len(c.Scp) != 0 {
			t.Fatalf("%s: a token in the URL was honoured: %+v", q, c)
		}
		info := readJSON[AuthInfo](t, h.do("GET", "/api/auth"+q+secret, "", nil))
		if info.Scheme != "anonymous" {
			t.Fatalf("%s on /api/auth: %+v", q, info)
		}
	}
	ws, _ := dialWS(t, h.srv.URL, "/ws/echo?token="+secret, h.origin())
	if ws == nil {
		t.Fatal("no handshake")
	}
	if code, _ := ws.closeCode(2 * time.Second); code != 4401 {
		t.Fatalf("?token= on a websocket: close %d", code)
	}
	// Nothing the front answers puts a credential in a URL.
	resp := h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, h.origin())
	if resp.Header.Get("Location") != "" {
		t.Fatal("login redirected")
	}
}

// §WP0-11 response headers.
func TestResponseHygiene(t *testing.T) {
	h := newHarness(t, Config{})
	resp := h.do("GET", "/api/cookie", "", nil)
	if resp.StatusCode != 200 || resp.Header.Get("Set-Cookie") != "" {
		t.Fatalf("Set-Cookie from the runner reached the client: %v", resp.Header)
	}
	if resp.Header.Get("X-Content-Type-Options") != "nosniff" || resp.Header.Get("Content-Security-Policy") != "sandbox" {
		t.Fatalf("proxied headers: %v", resp.Header)
	}
	resp = h.do("GET", "/api/auth", "", nil)
	if resp.Header.Get("Content-Security-Policy") != "frame-ancestors 'none'" || resp.Header.Get("X-Frame-Options") != "DENY" {
		t.Fatalf("front page headers: %v", resp.Header)
	}
}

// Merge requirement 10: the chain is tri-state.
func TestProviderChain(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	// A presented but invalid token is 401, never anonymous read.
	for _, auth := range []string{
		"Bearer " + store.TokenPrefix + strings.Repeat("A", 43), // well-formed, unknown
		"Bearer not-a-token", "Basic YWRtaW46eA==", "Bearer",
	} {
		resp := h.do("GET", "/api/echo", "", http.Header{"Authorization": {auth}})
		if resp.StatusCode != 401 {
			t.Errorf("Authorization %q: %d, want 401", auth, resp.StatusCode)
		}
		info := h.do("GET", "/api/auth", "", http.Header{"Authorization": {auth}})
		if info.StatusCode != 401 {
			t.Errorf("Authorization %q on /api/auth: %d", auth, info.StatusCode)
		}
	}
	// Nothing presented: anonymous read.
	if c := readJSON[echo](t, h.do("GET", "/api/echo", "", nil)).Claims; !reflect.DeepEqual(c.Scp, []string{"read"}) {
		t.Fatalf("anonymous read: %+v", c)
	}
	// A store failure is 503, not anonymous.
	cookie := h.login()
	secret := h.createToken(cookie, `{"name":"ci","scopes":["read"]}`)
	if err := os.WriteFile(h.opts.TokensPath, []byte("{broken"), 0o600); err != nil {
		t.Fatal(err)
	}
	if resp := h.do("GET", "/api/echo", "", bearer(secret, nil)); resp.StatusCode != 503 {
		t.Fatalf("broken tokens file: %d, want 503", resp.StatusCode)
	}

	// The proxy provider: Accept, Reject, error, NotMine.
	for name, c := range map[string]struct {
		client stubClient
		want   int
	}{
		"accept":   {stubClient{id: Identity{Issuer: "idp", Subject: "ben", Name: "Ben"}, outcome: Accept}, 200},
		"reject":   {stubClient{outcome: Reject}, 401},
		"error":    {stubClient{outcome: Reject, err: errors.New("JWKS unreachable")}, 503},
		"not mine": {stubClient{outcome: NotMine}, 200},
	} {
		t.Run(name, func(t *testing.T) {
			p := newProxyHarness(t, c.client, map[string][]string{"all": {"ben"}}, "none")
			resp := p.do("GET", "/api/echo", "", nil)
			if resp.StatusCode != c.want {
				t.Fatalf("%d, want %d", resp.StatusCode, c.want)
			}
			if c.want != 200 {
				return
			}
			cl := readJSON[echo](t, resp).Claims
			switch name {
			case "accept":
				if cl.Sub != "proxy:idp#ben" || cl.Nm != "Ben" || !reflect.DeepEqual(cl.Scp, []string{"operate", "read"}) || cl.Kind != "human" {
					t.Fatalf("claims %+v", cl)
				}
				info := readJSON[AuthInfo](t, p.do("GET", "/api/auth", "", nil))
				if info.Shape != "proxy" || info.Scheme != "proxy" || info.User == nil || info.User.ID != "proxy:idp#ben" {
					t.Fatalf("info %+v", info)
				}
			case "not mine":
				if cl.Sub != "anon:" || len(cl.Scp) != 0 {
					t.Fatalf("claims %+v", cl)
				}
			}
		})
	}
	// An unmatched proxy user gets read.
	p := newProxyHarness(t, stubClient{id: Identity{Subject: "dave"}, outcome: Accept}, map[string][]string{"all": {"ben"}}, "none")
	if cl := readJSON[echo](t, p.do("GET", "/api/echo", "", nil)).Claims; cl.Sub != "proxy:dave" || !reflect.DeepEqual(cl.Scp, []string{"read"}) {
		t.Fatalf("unmatched proxy user: %+v", cl)
	}
}

func newProxyHarness(t *testing.T, client stubClient, grants map[string][]string, anonymous string) *harness {
	t.Helper()
	c := Config{Auth: "proxy", Anonymous: anonymous, Proxy: &ProxyConfig{Preset: "custom", Grants: grants}}
	plan, cl := ResolveWith(c, false, func(*ProxyConfig, Plan) (Client, error) { return client, nil })
	if plan.Shape != "proxy" {
		t.Fatalf("plan %+v", plan)
	}
	return newHarness(t, c, func(o *Options) { o.Plan = plan; o.Proxy = cl })
}

// Merge requirement 12: effective token scopes = the stored ceiling ∩ the
// issuer's current verbs, per rig.
func TestTokenScopes(t *testing.T) {
	h := newHarness(t, Config{})
	cli, err := store.OpenTokens(h.opts.TokensPath, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	mk := func(scopes []string, issuer string) string {
		s, _, err := cli.Create(store.NewToken{Name: "t", Scopes: scopes, Issuer: issuer})
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	scp := func(secret string) []string {
		return readJSON[echo](t, h.do("GET", "/api/echo", "", bearer(secret, nil))).Claims.Scp
	}
	cases := []struct {
		scopes []string
		issuer string
		want   []string
	}{
		{[]string{"read:*"}, "", []string{"read"}},
		{[]string{"operate:blender", "read:*"}, "", []string{"operate", "read"}},
		{[]string{"operate:furnace", "read:furnace"}, "", []string{}},
		{[]string{"operate:*", "read:*"}, "local:admin", []string{"operate", "read"}},
		// Issued by a proxy identity whom the current grants give read only.
		{[]string{"operate:*", "read:*"}, "proxy:idp#dave", []string{"read"}},
		{[]string{"manage"}, "", []string{}},
	}
	for _, c := range cases {
		if got := scp(mk(c.scopes, c.issuer)); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%v from %q: %v, want %v", c.scopes, c.issuer, got, c.want)
		}
	}
	// The token's own identity reaches the principal.
	secret, tok, _ := cli.Create(store.NewToken{Name: "agent-1", Scopes: []string{"read"}, Kind: "agent"})
	c := readJSON[echo](t, h.do("GET", "/api/echo", "", bearer(secret, nil))).Claims
	if c.Sub != "token:agent-1" || c.Nm != "agent-1" || c.Kind != "agent" || c.Sid != tok.ID {
		t.Fatalf("token claims %+v", c)
	}
}

func TestTokenRoutes(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	if resp := h.do("GET", "/api/auth/tokens", "", nil); resp.StatusCode != 401 {
		t.Fatalf("anonymous list: %d", resp.StatusCode)
	}
	admin := h.login()
	resp := h.do("POST", "/api/auth/tokens", `{"name":"ci","scopes":["read"],"kind":"service","expires_in":86400}`, withCookie(admin, h.origin()))
	if resp.StatusCode != 201 {
		t.Fatalf("create: %d %s", resp.StatusCode, body(resp))
	}
	created := readJSON[map[string]any](t, resp)
	secret, _ := created["token"].(string)
	if !store.IsToken(secret) || created["name"] != "ci" || !reflect.DeepEqual(created["scopes"], []any{"read:*"}) || created["id"] == nil {
		t.Fatalf("created %v", created)
	}
	exp, _ := time.Parse(time.RFC3339, created["expires"].(string))
	if d := time.Until(exp); d < 23*time.Hour || d > 25*time.Hour {
		t.Fatalf("expires_in ignored: %v", d)
	}
	listResp := h.do("GET", "/api/auth/tokens", "", withCookie(admin, nil))
	rawList := body(listResp)
	if listResp.StatusCode != 200 || strings.Contains(rawList, secret) || !strings.Contains(rawList, `"name":"ci"`) {
		t.Fatalf("list: %d %s", listResp.StatusCode, rawList)
	}
	// A token cannot manage tokens, nor mint the management scope.
	if resp := h.do("GET", "/api/auth/tokens", "", bearer(secret, nil)); resp.StatusCode != 403 {
		t.Fatalf("token lists tokens: %d", resp.StatusCode)
	}
	if resp := h.do("POST", "/api/auth/tokens", `{"name":"m","scopes":["manage"]}`, withCookie(admin, h.origin())); resp.StatusCode != 403 {
		t.Fatalf("management scope from a session: %d", resp.StatusCode)
	}
	for _, bad := range []string{`{"name":"x","scopes":["fly"]}`, `{"name":"","scopes":["read"]}`, `{"name":"x","kind":"robot"}`, `{"name":"x","expires_in":-1}`, `nope`} {
		if resp := h.do("POST", "/api/auth/tokens", bad, withCookie(admin, h.origin())); resp.StatusCode != 400 {
			t.Errorf("%s: %d, want 400", bad, resp.StatusCode)
		}
	}
	if resp := h.do("DELETE", "/api/auth/tokens/0000000000000000", "", withCookie(admin, h.origin())); resp.StatusCode != 404 {
		t.Fatalf("unknown id: %d", resp.StatusCode)
	}
	// The local shape manages tokens without a login.
	l := newHarness(t, Config{})
	if resp := l.do("POST", "/api/auth/tokens", `{"name":"ci"}`, l.origin()); resp.StatusCode != 201 {
		t.Fatalf("local create: %d %s", resp.StatusCode, body(resp))
	}
}

// Merge requirement 27: an old runner is never proxied to.
func TestTooOldRunner(t *testing.T) {
	h := newHarness(t, Config{})
	h.runner.tooOld = true
	resp := h.do("GET", "/api/echo", "", nil)
	if resp.StatusCode != 502 || !strings.Contains(body(resp), "too old") {
		t.Fatalf("%d", resp.StatusCode)
	}
	for _, r := range h.runner.requests() {
		if r.Path != "/api/auth/front" {
			t.Fatalf("an old runner was proxied to: %s", r.Path)
		}
	}
}

func TestRunnerStarting(t *testing.T) {
	h := newHarness(t, Config{}, func(o *Options) {
		o.Route = SingleRig(Rig{Name: "r", Target: func(context.Context) (Target, error) {
			return Target{Endpoint: endpointAt(filepath.Join(os.TempDir(), "fb-nothing-here.sock")), Aud: "a", Key: principal.Key{1}}, nil
		}})
	})
	resp := h.do("GET", "/api/echo", "", nil)
	if resp.StatusCode != 503 || resp.Header.Get("Retry-After") == "" {
		t.Fatalf("%d", resp.StatusCode)
	}
	h2 := newHarness(t, Config{}, func(o *Options) {
		o.Route = SingleRig(Rig{Name: "r", Target: func(context.Context) (Target, error) { return Target{}, ErrStarting }})
	})
	if resp := h2.do("GET", "/api/echo", "", nil); resp.StatusCode != 503 {
		t.Fatalf("ErrStarting: %d", resp.StatusCode)
	}
}

// Several rigs under one front (flyballd): each gets its own aud and its
// own verbs, and paths no rig owns go to the fallback handler.
func TestRouting(t *testing.T) {
	a, b := newFakeRunner(t, "alpha"), newFakeRunner(t, "beta")
	a.root, b.root = "/alpha", "/beta"
	rigs := map[string]Rig{
		"/alpha": {Root: "/alpha", Name: "alpha", Target: func(context.Context) (Target, error) { return a.target(), nil }},
		"/beta":  {Root: "/beta", Name: "beta", Target: func(context.Context) (Target, error) { return b.target(), nil }},
	}
	h := newHarness(t, Config{}, func(o *Options) {
		o.Route = func(path string) (Rig, bool) {
			for root, r := range rigs {
				if path == root || strings.HasPrefix(path, root+"/") {
					return r, true
				}
			}
			return Rig{}, false
		}
		o.Fallback = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("landing")) })
	})
	cli, _ := store.OpenTokens(h.opts.TokensPath, store.TokensOptions{})
	secret, _, _ := cli.Create(store.NewToken{Name: "t", Scopes: []string{"read:*", "operate:beta"}})
	ca := readJSON[echo](t, h.do("GET", "/alpha/api/echo", "", bearer(secret, nil)))
	cb := readJSON[echo](t, h.do("GET", "/beta/api/echo", "", bearer(secret, nil)))
	if ca.Claims.Aud != "alpha" || !reflect.DeepEqual(ca.Claims.Scp, []string{"read"}) || ca.URI != "/alpha/api/echo" {
		t.Fatalf("alpha: %+v %s", ca.Claims, ca.URI)
	}
	if cb.Claims.Aud != "beta" || !reflect.DeepEqual(cb.Claims.Scp, []string{"operate", "read"}) {
		t.Fatalf("beta: %+v", cb.Claims)
	}
	info := readJSON[AuthInfo](t, h.do("GET", "/beta/api/auth", "", bearer(secret, nil)))
	if !reflect.DeepEqual(info.Verbs, []string{"operate", "read"}) {
		t.Fatalf("beta verbs %v", info.Verbs)
	}
	if info.Rig != "beta" {
		t.Fatalf("beta rig = %q, want beta", info.Rig)
	}
	if b := body(h.do("GET", "/", "", nil)); b != "landing" {
		t.Fatalf("fallback: %q", b)
	}
	rootResp := h.do("GET", "/api/auth", "", nil)
	if rootResp.StatusCode != 200 {
		t.Fatalf("root /api/auth: %d", rootResp.StatusCode)
	}
	if root := readJSON[AuthInfo](t, rootResp); root.Rig != "" {
		t.Fatalf("root rig = %q, want empty (flyballd's own root names no rig)", root.Rig)
	}
	if resp := h.do("GET", "/alpha/", "", nil); resp.StatusCode != 200 || body(resp) != testUI {
		t.Fatalf("UI under a root: %d", resp.StatusCode)
	}
}

// The readiness probe the front hands the backend verifies at a runner.
func TestProbeSigner(t *testing.T) {
	fr := newFakeRunner(t, "probe-aud")
	info, err := handshake(fr, ProbeSigner(time.Now))
	if err != nil || info.Aud != "probe-aud" {
		t.Fatalf("%+v %v", info, err)
	}
}
