package front

import (
	"net/http"
	"strings"
	"testing"
)

// An Authorization: Basic header is never a flyball credential; it comes
// from a proxy doing HTTP Basic auth (nginx auth_basic) and forwarding the
// browser's header. It is refused like any unrecognised credential, but
// the 401 says so and says what to do: clear it at the proxy.
func TestBasicAuthorizationSaysToClearItAtTheProxy(t *testing.T) {
	h := newHarness(t, Config{Auth: "password", Password: testScrypt, Anonymous: "read"})
	cookie := h.login()
	basic := http.Header{"Authorization": {"Basic YWRtaW46eA=="}}
	for name, c := range map[string]struct {
		path string
		hdr  http.Header
	}{
		"anonymous":         {"/api/echo", basic},
		"GET /api/auth":     {"/api/auth", basic},
		"beside a session":  {"/api/echo", withCookie(cookie, basic.Clone())},
		"lower-case scheme": {"/api/echo", http.Header{"Authorization": {"basic YWRtaW46eA=="}}},
	} {
		t.Run(name, func(t *testing.T) {
			resp := h.do("GET", c.path, "", c.hdr)
			if resp.StatusCode != 401 {
				t.Fatalf("%d, want 401", resp.StatusCode)
			}
			d := readJSON[map[string]string](t, resp)["detail"]
			if !strings.Contains(d, "Basic") || !strings.Contains(d, "proxy") {
				t.Fatalf("detail %q does not say a proxy should clear Authorization: Basic", d)
			}
		})
	}
	// Any other unrecognised credential keeps the plain detail.
	resp := h.do("GET", "/api/echo", "", http.Header{"Authorization": {"Bearer not-a-token"}})
	if d := readJSON[map[string]string](t, resp)["detail"]; resp.StatusCode != 401 || d != "Unrecognised credential" {
		t.Fatalf("Bearer not-a-token: %d %q", resp.StatusCode, d)
	}
}

// nginx as the gate (auth_basic, then the custom preset reading
// Remote-User): the proxy shape lets the forwarded Basic header through
// to the preset, which vouches for the user.
func TestProxyShapeServesBesideABasicHeader(t *testing.T) {
	p := newProxyHarness(t, stubClient{id: Identity{Issuer: "custom", Subject: "ben"}, outcome: Accept},
		map[string][]string{"all": {"ben"}}, "none")
	if resp := p.do("GET", "/api/echo", "", http.Header{"Authorization": {"Basic YmVuOng="}}); resp.StatusCode != 200 {
		t.Fatalf("%d, want 200", resp.StatusCode)
	}
}
