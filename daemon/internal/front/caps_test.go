package front

import (
	"net/http"
	"strings"
	"testing"
	"time"
)

// D-047 item 1: a caller with no verb on the rig is refused by the front
// with the answer the runner would give -- 401 (a socket closed 4401, which
// the UI takes as "sign in") for no credential, 403 {detail, needed} (4403)
// for one that holds nothing here -- and the runner is never dialled.
func TestNoVerbCallerNeverReachesRunner(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt}) // anonymous: none
	cookie := h.login()
	elsewhere := h.createToken(cookie, `{"name":"elsewhere","scopes":["read:other","operate:other"]}`)

	// No credential.
	for _, c := range []struct{ method, path, body string }{
		{"GET", "/api/guarded", ""}, {"POST", "/api/guarded", "{}"}, {"POST", "/api/rig/stop", "{}"}, {"POST", "/mcp/read", "{}"},
	} {
		resp := h.do(c.method, c.path, c.body, h.origin())
		if resp.StatusCode != 401 || resp.Header.Get("WWW-Authenticate") != "Bearer" {
			t.Errorf("anonymous %s %s: %d, WWW-Authenticate %q; want 401 Bearer", c.method, c.path, resp.StatusCode, resp.Header.Get("WWW-Authenticate"))
		}
	}
	ws, resp := dialWS(t, h.srv.URL, "/ws/echo", h.origin())
	if ws == nil {
		t.Fatalf("anonymous socket: HTTP %d, want a completed handshake", resp.StatusCode)
	}
	if code, _ := ws.closeCode(2 * time.Second); code != 4401 {
		t.Fatalf("anonymous socket: close %d, want 4401", code)
	}

	// A credential with no verb on this rig.
	for _, c := range []struct{ method, path, body, needed string }{
		{"GET", "/api/guarded", "", "read"}, {"POST", "/api/rig/stop", "{}", "operate"},
	} {
		resp := h.do(c.method, c.path, c.body, bearer(elsewhere, nil))
		b := body(resp)
		if resp.StatusCode != 403 || !strings.Contains(b, `"needed":"`+c.needed+`"`) {
			t.Errorf("token for another rig, %s %s: %d %s; want 403 needing %s", c.method, c.path, resp.StatusCode, b, c.needed)
		}
	}
	ws, resp = dialWS(t, h.srv.URL, "/ws/echo", bearer(elsewhere, nil))
	if ws == nil {
		t.Fatalf("token for another rig, socket: HTTP %d, want a completed handshake", resp.StatusCode)
	}
	if code, _ := ws.closeCode(2 * time.Second); code != 4403 {
		t.Fatalf("token for another rig, socket: close %d, want 4403", code)
	}

	if n := h.runner.hits.Load(); n != 0 {
		t.Fatalf("the runner was dialled %d times for callers with no verb on it", n)
	}
}

// holdWS opens a held socket and waits for the runner's first frame, so
// it is known to be bridged.
func holdWS(t *testing.T, h *harness, hdr http.Header) *wsClient {
	t.Helper()
	ws, resp := dialWS(t, h.srv.URL, "/ws/hold", hdr)
	if ws == nil {
		t.Fatalf("socket: HTTP %d", resp.StatusCode)
	}
	if op, p, err := ws.next(5 * time.Second); err != nil || op != 1 || string(p) != "hello" {
		t.Fatalf("socket not bridged: op %d %q %v", op, p, err)
	}
	return ws
}

// refusedTryLater: the socket's handshake completes and it is closed 1013.
func refusedTryLater(t *testing.T, h *harness, name string, hdr http.Header) {
	t.Helper()
	ws, resp := dialWS(t, h.srv.URL, "/ws/hold", hdr)
	if ws == nil {
		t.Fatalf("%s: HTTP %d, want a completed handshake closed 1013", name, resp.StatusCode)
	}
	if code, _ := ws.closeCode(2 * time.Second); code != 1013 {
		t.Fatalf("%s: close %d, want 1013 (try again later)", name, code)
	}
}

// D-047 item 1: at most 128 held connections per rig from callers with no
// credential. The 129th anonymous socket is closed 1013 and an anonymous
// event stream is 429; a signed-in caller still connects, and a plain GET
// is not held and not counted.
func TestAnonymousHeldCap(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	cookie := h.login()
	var first *wsClient
	for i := range 128 {
		ws := holdWS(t, h, h.origin())
		if i == 0 {
			first = ws
		}
	}
	refusedTryLater(t, h, "129th anonymous socket", h.origin())
	holdWS(t, h, withCookie(cookie, h.origin()))

	resp := h.do("GET", "/api/stream", "", http.Header{"Accept": {"text/event-stream"}})
	if resp.StatusCode != 429 || resp.Header.Get("Retry-After") == "" {
		t.Fatalf("anonymous event stream over the cap: %d, Retry-After %q; want 429 with one", resp.StatusCode, resp.Header.Get("Retry-After"))
	}
	if resp := h.do("GET", "/api/guarded", "", nil); resp.StatusCode != 200 {
		t.Fatalf("anonymous plain GET at the cap: %d, want 200", resp.StatusCode)
	}

	// A socket that closes gives its place back.
	first.conn.Close()
	deadline := time.Now().Add(5 * time.Second)
	for {
		ws, _ := dialWS(t, h.srv.URL, "/ws/hold", h.origin())
		if ws == nil {
			t.Fatal("no handshake")
		}
		if op, _, err := ws.next(2 * time.Second); err == nil && op == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("a closed anonymous socket's place was not given back")
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// D-047 item 1: at most 512 held connections per rig in all. At the cap
// every new socket is closed 1013 and an event stream is 429, but the stop
// -- a POST -- still reaches the runner.
func TestStopAtTheHeldCap(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	cookie := h.login()
	for range 128 {
		holdWS(t, h, h.origin())
	}
	for range 512 - 128 {
		holdWS(t, h, withCookie(cookie, h.origin()))
	}
	refusedTryLater(t, h, "513th socket, signed in", withCookie(cookie, h.origin()))
	refusedTryLater(t, h, "513th socket, anonymous", h.origin())
	resp := h.do("GET", "/api/stream", "", withCookie(cookie, http.Header{"Accept": {"text/event-stream"}}))
	if resp.StatusCode != 429 || resp.Header.Get("Retry-After") == "" {
		t.Fatalf("event stream at the cap: %d, Retry-After %q; want 429 with one", resp.StatusCode, resp.Header.Get("Retry-After"))
	}
	before := h.runner.hits.Load()
	resp = h.do("POST", "/api/rig/stop", "{}", withCookie(cookie, h.origin()))
	if b := body(resp); resp.StatusCode != 200 || !strings.Contains(b, "stopping") {
		t.Fatalf("stop at the cap: %d %s, want the runner's 200", resp.StatusCode, b)
	}
	if h.runner.hits.Load() != before+1 {
		t.Fatal("the stop did not reach the runner")
	}
}
