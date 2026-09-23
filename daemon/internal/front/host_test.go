package front

import (
	"bufio"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"
)

// sendHost sends one request to the harness's front with Host set to host.
func (h *harness) sendHost(method, path, host, reqBody string, hdr http.Header) *http.Response {
	h.t.Helper()
	var req *http.Request
	var err error
	if reqBody != "" {
		req, err = http.NewRequest(method, h.srv.URL+path, strings.NewReader(reqBody))
		req.Header.Set("Content-Type", "application/json")
	} else {
		req, err = http.NewRequest(method, h.srv.URL+path, nil)
	}
	if err != nil {
		h.t.Fatal(err)
	}
	req.Host = host
	for k, v := range hdr {
		req.Header[k] = v
	}
	resp, err := noRedirect.Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	h.t.Cleanup(func() { resp.Body.Close() })
	return resp
}

// dialWSHost asks for a websocket upgrade of path with Host set to host;
// a 101 is returned as a non-nil conn.
func dialWSHost(t *testing.T, base, path, host string, hdr http.Header) (net.Conn, *http.Response) {
	t.Helper()
	u, _ := url.Parse(base)
	conn, err := net.Dial("tcp", u.Host)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	fmt.Fprintf(conn, "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"+
		"Sec-WebSocket-Key: MDEyMzQ1Njc4OWFiY2RlZg==\r\nSec-WebSocket-Version: 13\r\n", path, host)
	for k, vs := range hdr {
		for _, v := range vs {
			fmt.Fprintf(conn, "%s: %s\r\n", k, v)
		}
	}
	fmt.Fprint(conn, "\r\n")
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	resp, err := http.ReadResponse(bufio.NewReader(conn), &http.Request{Method: "GET"})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode == http.StatusSwitchingProtocols {
		return conn, resp
	}
	return nil, resp
}

// rebound is what a page's fetch() sends once its name points at the
// front: Host and Origin both the attacker's, and to the browser a
// same-origin request.
func rebound(host string) http.Header {
	return http.Header{"Origin": {"http://" + host}, "Sec-Fetch-Site": {"same-origin"}}
}

// insecureOpen is the local shape served beyond loopback by opt-in
// (`--insecure-open`), optionally with url:.
func insecureOpen(url string) func(*Options) {
	return func(o *Options) { o.Plan = Resolve(Config{Listen: "0.0.0.0:9000", URL: url}, true) }
}

// ownNamesForTest is this machine's names as D-043 counts them.
func ownNamesForTest(t *testing.T) []string {
	t.Helper()
	name, err := os.Hostname()
	if err != nil || name == "" {
		t.Skip("no hostname")
	}
	name = strings.ToLower(name)
	first, _, _ := strings.Cut(name, ".")
	return []string{name, first, name + ".local", first + ".local"}
}

// D-043: under --insecure-open the local shape's console (every verb)
// answers only a name no page elsewhere can own, on every route --
// challenge-b decision 4's reproduction: a rebound page stopped the rig
// and minted an operate token.
func TestInsecureOpenRefusesRebinding(t *testing.T) {
	h := newHarness(t, Config{}, insecureOpen(""))
	evil := "evil.example:9000"

	resp := h.sendHost("POST", "/api/rig/stop", evil, "{}", rebound(evil))
	if resp.StatusCode != 403 {
		t.Errorf("rebound POST /api/rig/stop: %d %s, want 403", resp.StatusCode, body(resp))
	}
	if b := body(resp); !strings.Contains(b, "IP address") || !strings.Contains(b, "url:") {
		t.Errorf("the 403 does not say which names it takes: %q", b)
	}
	resp = h.sendHost("POST", "/api/auth/tokens", evil, `{"name":"x","scopes":["operate"]}`, rebound(evil))
	if resp.StatusCode != 403 {
		t.Errorf("rebound POST /api/auth/tokens: %d %s, want 403", resp.StatusCode, body(resp))
	}
	for _, path := range []string{"/api/auth", "/api/echo", "/"} {
		if resp := h.sendHost("GET", path, evil, "", nil); resp.StatusCode != 403 {
			t.Errorf("rebound GET %s: %d, want 403", path, resp.StatusCode)
		}
	}
	if ws, resp := dialWSHost(t, h.srv.URL, "/ws/echo", evil, rebound(evil)); ws != nil || resp.StatusCode != 403 {
		t.Errorf("rebound websocket was not refused")
	}
	if n := len(h.runner.requests()); n != 0 {
		t.Fatalf("the runner received %d requests from a rebound page", n)
	}

	// The names it does answer, on any port.
	known := append([]string{"192.168.1.20:9000", "192.168.1.20", "[fe80::1]:9000", "10.0.0.7:80",
		"localhost:9000", "127.0.0.1", "[::1]:9000"}, ownNamesForTest(t)...)
	for _, host := range known {
		if resp := h.sendHost("GET", "/api/echo", host, "", nil); resp.StatusCode != 200 {
			t.Errorf("Host %s: GET %d, want 200", host, resp.StatusCode)
		}
		if resp := h.sendHost("POST", "/api/echo", host, "{}", rebound(host)); resp.StatusCode != 200 {
			t.Errorf("Host %s: same-site POST %d, want 200", host, resp.StatusCode)
		}
	}
	// A bearer token is no exemption here: in the local shape a script
	// uses an IP address or url:.
	resp = h.sendHost("POST", "/api/auth/tokens", "127.0.0.1:9000", `{"name":"ci","scopes":["operate:*"]}`, rebound("127.0.0.1:9000"))
	if resp.StatusCode != 201 {
		t.Fatalf("token by IP: %d %s", resp.StatusCode, body(resp))
	}
	secret := readJSON[struct {
		Token string `json:"token"`
	}](t, resp).Token
	if resp := h.sendHost("GET", "/api/echo", evil, "", bearer(secret, nil)); resp.StatusCode != 403 {
		t.Errorf("bearer by a foreign name under --insecure-open: %d, want 403", resp.StatusCode)
	}
}

// D-043: url: widens the names an --insecure-open front answers (it was an
// empty case in ResolveWith), and only by its authority.
func TestInsecureOpenURLWidens(t *testing.T) {
	if p := Resolve(Config{Listen: "0.0.0.0:9000", URL: "http://rig.lab.example:9000"}, true); p.Fallback != "" {
		t.Fatalf("plan %+v", p)
	}
	h := newHarness(t, Config{}, insecureOpen("http://rig.lab.example:9000"))
	for host, want := range map[string]int{"rig.lab.example:9000": 200, "RIG.lab.example:9000": 200,
		"rig.lab.example:9001": 403, "evil.example:9000": 403, "192.168.1.20:9000": 200} {
		if resp := h.sendHost("GET", "/api/echo", host, "", nil); resp.StatusCode != want {
			t.Errorf("Host %s: %d, want %d", host, resp.StatusCode, want)
		}
	}
	if resp := h.sendHost("POST", "/api/echo", "rig.lab.example:9000", "{}", rebound("rig.lab.example:9000")); resp.StatusCode != 200 {
		t.Errorf("url: host POST: %d", resp.StatusCode)
	}
}

// The plain local shape (loopback) keeps its loopback-only rule: an IP
// address or the machine's name is not loopback.
func TestLocalShapeStaysLoopbackOnly(t *testing.T) {
	h := newHarness(t, Config{})
	for _, host := range append([]string{"192.168.1.20", "10.0.0.7:8000"}, ownNamesForTest(t)...) {
		if host == "localhost" {
			continue
		}
		if resp := h.sendHost("GET", "/api/echo", host, "", nil); resp.StatusCode != 403 {
			t.Errorf("Host %s: %d, want 403", host, resp.StatusCode)
		}
	}
}

// D-043: on a credential shape without url:, an anonymous caller is served
// only by a known name on the proxied routes; sign-in and the UI answer
// any name, and a signed-in caller or a token passes any Host.
func TestAnonymousKnownHostsOnly(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	evil := "evil.example:8000"

	resp := h.sendHost("GET", "/api/echo", evil, "", rebound(evil))
	if resp.StatusCode != 403 {
		t.Fatalf("rebound anonymous read: %d %s, want 403", resp.StatusCode, body(resp))
	}
	if b := body(resp); !strings.Contains(b, "IP address") || !strings.Contains(b, "sign in") {
		t.Errorf("the 403 does not say what to do: %q", b)
	}
	if ws, resp := dialWSHost(t, h.srv.URL, "/ws/echo", evil, rebound(evil)); ws != nil || resp.StatusCode != 403 {
		t.Errorf("rebound anonymous websocket was not refused")
	}
	if n := len(h.runner.requests()); n != 0 {
		t.Fatalf("the runner received %d requests from a rebound page", n)
	}
	for _, host := range append([]string{"192.168.1.20:8000", "[fe80::1]:8000", "localhost:8000"}, ownNamesForTest(t)...) {
		if resp := h.sendHost("GET", "/api/echo", host, "", nil); resp.StatusCode != 200 {
			t.Errorf("anonymous by %s: %d, want 200", host, resp.StatusCode)
		}
	}

	// Sign-in and the UI's own files answer any name.
	if resp := h.sendHost("GET", "/", evil, "", nil); resp.StatusCode != 200 {
		t.Errorf("UI by a foreign name: %d", resp.StatusCode)
	}
	if resp := h.sendHost("GET", "/api/auth", evil, "", nil); resp.StatusCode != 200 {
		t.Errorf("GET /api/auth by a foreign name: %d", resp.StatusCode)
	}
	cookie := h.login2(http.Header{"Host": {"rig.site.example:8000"}, "Origin": {"http://rig.site.example:8000"}})

	// Signed in, any name; a token too.
	if resp := h.sendHost("GET", "/api/echo", "rig.site.example:8000", "", withCookie(cookie, nil)); resp.StatusCode != 200 {
		t.Errorf("session by a site name: %d", resp.StatusCode)
	}
	if resp := h.sendHost("POST", "/api/echo", "rig.site.example:8000", "{}",
		withCookie(cookie, http.Header{"Origin": {"http://rig.site.example:8000"}})); resp.StatusCode != 200 {
		t.Errorf("session act by a site name: %d", resp.StatusCode)
	}
	secret := h.createToken(cookie, `{"name":"ci","scopes":["read:*"]}`)
	if resp := h.sendHost("GET", "/api/echo", evil, "", bearer(secret, nil)); resp.StatusCode != 200 {
		t.Errorf("token by a foreign name: %d", resp.StatusCode)
	}
}
