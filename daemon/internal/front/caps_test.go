package front

import (
	"bufio"
	"fmt"
	"net"
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
// event stream is 429; a signed-in caller still connects.
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
	// Every request from a caller with no credential and no operate is
	// counted (wave 3 F1); a signed-in caller's plain GET is not.
	if resp := h.do("GET", "/api/guarded", "", nil); resp.StatusCode != 429 {
		t.Fatalf("anonymous plain GET at the cap: %d, want 429", resp.StatusCode)
	}
	if resp := h.do("GET", "/api/guarded", "", withCookie(cookie, nil)); resp.StatusCode != 200 {
		t.Fatalf("signed-in plain GET at the anonymous cap: %d, want 200", resp.StatusCode)
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

// slowPost opens a POST to path on the front announcing a large body and
// sends only its first byte, as a caller trickling it would.
func slowPost(t *testing.T, h *harness, path string, hdr http.Header) net.Conn {
	t.Helper()
	addr := strings.TrimPrefix(h.srv.URL, "http://")
	c, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	var b strings.Builder
	fmt.Fprintf(&b, "POST %s HTTP/1.1\r\nHost: %s\r\nContent-Type: application/json\r\nContent-Length: 1000000\r\n", path, addr)
	for k, vs := range hdr {
		for _, v := range vs {
			fmt.Fprintf(&b, "%s: %s\r\n", k, v)
		}
	}
	b.WriteString("\r\n{")
	if _, err := c.Write([]byte(b.String())); err != nil {
		t.Fatal(err)
	}
	return c
}

// waitFor polls n until it reaches want or d passes, and returns its last
// value.
func waitFor(n func() int64, want int64, d time.Duration) int64 {
	deadline := time.Now().Add(d)
	for n() < want && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	return n()
}

// Wave 3 F1: a caller with no credential and no operate (anonymous: read)
// is counted in the anonymous pool on every request, whatever the method:
// 600 slow-body POSTs to a read route hold at most 128 runner connections,
// the rest are 429. A signed-in caller's POSTs are never counted: its own
// slow POST and the stop still reach the runner.
func TestAnonymousSlowBodiesCapped(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	cookie := h.login()
	for range 600 {
		slowPost(t, h, "/mcp/body", h.origin())
	}
	waitFor(h.runner.bodies.Load, maxHeldAnonymous, 5*time.Second)
	time.Sleep(500 * time.Millisecond) // for any over the cap to arrive
	got := h.runner.bodies.Load()
	t.Logf("anonymous slow-body POSTs held at the runner: %d (cap %d)", got, maxHeldAnonymous)
	if got != maxHeldAnonymous {
		t.Fatalf("600 anonymous slow POSTs hold %d runner connections; want the anonymous cap, %d", got, maxHeldAnonymous)
	}
	resp := h.do("POST", "/mcp/body", "{}", h.origin())
	if resp.StatusCode != 429 || resp.Header.Get("Retry-After") == "" {
		t.Fatalf("anonymous POST at the cap: %d, Retry-After %q; want 429 with one", resp.StatusCode, resp.Header.Get("Retry-After"))
	}

	slowPost(t, h, "/mcp/body", withCookie(cookie, h.origin()))
	if got := waitFor(h.runner.bodies.Load, maxHeldAnonymous+1, 3*time.Second); got != maxHeldAnonymous+1 {
		t.Fatalf("a signed-in slow POST at the anonymous cap: %d held, want %d (it is never counted)", got, maxHeldAnonymous+1)
	}
	resp = h.do("POST", "/api/rig/stop", "{}", withCookie(cookie, h.origin()))
	if b := body(resp); resp.StatusCode != 200 || !strings.Contains(b, "stopping") {
		t.Fatalf("stop during the flood: %d %s, want the runner's 200", resp.StatusCode, b)
	}
}

// Wave 3 F1: a request body that stalls is cut off BodyTimeout after the
// request arrived (408), and its runner connection is let go, whoever
// sends it. A body that arrives in time lifts the deadline, so a slow
// answer is not cut; a websocket and an event stream never get one.
func TestStalledBodyCut(t *testing.T) {
	old := BodyTimeout
	BodyTimeout = 300 * time.Millisecond
	t.Cleanup(func() { BodyTimeout = old })
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	cookie := h.login()

	c := slowPost(t, h, "/mcp/body", withCookie(cookie, h.origin()))
	if got := waitFor(h.runner.bodies.Load, 1, 3*time.Second); got != 1 {
		t.Fatalf("the slow POST did not reach the runner: %d", got)
	}
	start := time.Now()
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	resp, err := http.ReadResponse(bufio.NewReader(c), &http.Request{Method: "POST"})
	if err != nil {
		t.Fatalf("a stalled body got no answer: %v", err)
	}
	if resp.StatusCode != http.StatusRequestTimeout {
		t.Fatalf("a stalled body: %d %s, want 408", resp.StatusCode, body(resp))
	}
	t.Logf("stalled body answered %d after %v", resp.StatusCode, time.Since(start).Round(time.Millisecond))
	if got := waitFor(func() int64 { return -h.runner.bodies.Load() }, 0, 3*time.Second); got != 0 {
		t.Fatalf("the runner still reads %d stalled bodies", -got)
	}

	// A body that arrived: the deadline is lifted for the answer (1.5 s).
	resp = h.do("POST", "/api/slow", "{}", withCookie(cookie, h.origin()))
	if b := body(resp); resp.StatusCode != 200 || b != "done" {
		t.Fatalf("a slow answer to a whole body: %d %q, want 200 done", resp.StatusCode, b)
	}

	// A socket outlives the deadline.
	ws := holdWS(t, h, withCookie(cookie, h.origin()))
	time.Sleep(3 * BodyTimeout)
	ws2, resp := dialWS(t, h.srv.URL, "/ws/echo", withCookie(cookie, h.origin()))
	if ws2 == nil {
		t.Fatalf("socket: HTTP %d", resp.StatusCode)
	}
	ws2.next(2 * time.Second) // hello
	time.Sleep(3 * BodyTimeout)
	if err := writeClientFrame(ws2.conn, 0x1, []byte("ping")); err != nil {
		t.Fatal(err)
	}
	if op, p, err := ws2.next(2 * time.Second); err != nil || op != 1 || string(p) != "ping" {
		t.Fatalf("a socket past the body deadline: op %d %q %v, want the echo", op, p, err)
	}
	if err := writeClientFrame(ws.conn, 0x9, nil); err != nil {
		t.Fatalf("the held socket was cut: %v", err)
	}

	// An event stream outlives it too.
	req, _ := http.NewRequest("GET", h.srv.URL+"/api/stream", nil)
	req.Header.Set("Accept", "text/event-stream")
	req.AddCookie(cookie)
	sresp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer sresp.Body.Close()
	br := bufio.NewReader(sresp.Body)
	until := time.Now().Add(4 * BodyTimeout)
	for time.Now().Before(until) {
		if _, err := br.ReadString('\n'); err != nil {
			t.Fatalf("the event stream ended past the body deadline: %v", err)
		}
	}
}
