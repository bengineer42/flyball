package proxyauth

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"flyballd/internal/front"
)

// Every accepted identity is proxy:<issuer>#<subject>; for the unsigned
// presets the issuer is the preset.

func TestTailscaleOverUnixSocket(t *testing.T) {
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{Preset: "tailscale"}}, Options{})
	e := rg.mustSub(h("Tailscale-User-Login", "ben@github", "Tailscale-User-Name", "Ben L"), "proxy:tailscale#ben@github")
	if e.Claims.Nm != "Ben L" {
		t.Errorf("name %q", e.Claims.Nm)
	}
	if !hasScope(e, "read") || hasScope(e, "operate") {
		t.Errorf("an unmatched user gets read only: %v", e.Claims.Scp)
	}
	noneOf(t, e.Headers, "Tailscale-User-Login", "Tailscale-User-Name")
	// A tagged device or Funnel: no identity headers, so anonymous.
	rg.mustAnonymous(nil)
	// A name without a login is not an identity: refused, not guessed.
	rg.mustStatus(h("Tailscale-User-Name", "Ben L"), 401)
	// Non-ASCII names arrive Q-encoded [Unverified: Tailscale's encoding].
	e = rg.mustSub(h("Tailscale-User-Login", "zoe@github", "Tailscale-User-Name", "=?utf-8?q?Zo=C3=AB?="), "proxy:tailscale#zoe@github")
	if e.Claims.Nm != "Zoë" {
		t.Errorf("decoded name %q", e.Claims.Nm)
	}
}

func TestAutheliaGrantsAndEmail(t *testing.T) {
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "authelia",
		Grants: map[string][]string{"all": {"group:lab", "boss@lab.org"}},
	}}, Options{})
	// A matched group gets the role's verbs.
	e := rg.mustSub(h("Remote-User", "ben", "Remote-Groups", "staff, lab", "Remote-Name", "Ben", "Remote-Email", "ben@lab.org"), "proxy:authelia#ben")
	if !hasScope(e, "operate") || !hasScope(e, "read") {
		t.Errorf("group lab grants all: %v", e.Claims.Scp)
	}
	noneOf(t, e.Headers, "Remote-User", "Remote-Groups", "Remote-Name", "Remote-Email", SecretHeader)
	// Email is never the subject and never grants: the grant names this
	// email, the user carries it, and gets read only.
	e = rg.mustSub(h("Remote-User", "eve", "Remote-Groups", "visitors", "Remote-Email", "boss@lab.org"), "proxy:authelia#eve")
	if hasScope(e, "operate") || !hasScope(e, "read") {
		t.Errorf("email granted: %v", e.Claims.Scp)
	}
	// Groups without a user: refused.
	rg.mustStatus(h("Remote-Groups", "lab"), 401)
	// Two users: ambiguous, refused.
	rg.mustStatus(map[string][]string{"Remote-User": {"ben", "eve"}}, 401)
	// A control character in a name: refused (it could not be minted).
	rg.mustStatus(h("Remote-User", "ben\u2028admin"), 401)
}

// A vouched-for peer that forwards a client's underscore or non-canonical
// copy of an identity header alongside its own (Caddy CVE-2026-52845's
// shape): refused, whatever the real header says.
func TestUnderscoreVariantsRefused(t *testing.T) {
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "authelia", Grants: map[string][]string{"all": {"group:admins"}},
	}}, Options{})
	for _, bad := range []map[string][]string{
		{"Remote-User": {"ben"}, "Remote_Groups": {"admins"}},
		{"Remote-User": {"ben"}, "Remote_groups": {"admins"}},
		{"Remote_User": {"admin"}},
		{"REMOTE_USER": {"admin"}},
		{"Remote-User": {"ben"}, "X-Flyball-Proxy_Secret": {"x"}},
	} {
		code, e := rg.get(bad)
		if code != 401 {
			t.Errorf("%v: status %d sub %q, want 401", bad, code, e.Claims.Sub)
		}
	}
	// A lower-case but otherwise canonical spelling is the same header.
	rg.mustSub(map[string][]string{"remote-user": {"ben"}}, "proxy:authelia#ben")
}

// from: unix records the peer's uid from SO_PEERCRED in the audit.
func TestUnixPeerUIDRecorded(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	audit, err := front.OpenAudit(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { audit.Close() })
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{Preset: "authelia"}}, Options{Audit: audit})
	rg.mustSub(h("Remote-User", "ben"), "proxy:authelia#ben")
	rg.mustSub(h("Remote-User", "ben"), "proxy:authelia#ben")
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var n int
	for _, line := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		var rec map[string]any
		if json.Unmarshal([]byte(line), &rec) != nil || rec["event"] != "proxy.peer" {
			continue
		}
		n++
		if rec["uid"] != strconv.Itoa(os.Getuid()) || rec["sub"] != "proxy:authelia#ben" {
			t.Errorf("record %v: want uid %d", rec, os.Getuid())
		}
	}
	if n != 1 {
		t.Errorf("%d proxy.peer records, want 1 (first sight of a uid and subject):\n%s", n, b)
	}
}

func TestOAuth2ProxyUnsignedGroupsMultiValued(t *testing.T) {
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "oauth2-proxy", Grants: map[string][]string{"all": {"group:ops"}},
	}}, Options{})
	// oauth2-proxy calls header.Add once per group; a comma list also counts.
	e := rg.mustSub(map[string][]string{"X-Forwarded-User": {"ben"}, "X-Forwarded-Groups": {"lab", "ops,staff"}}, "proxy:oauth2-proxy#ben")
	if !hasScope(e, "operate") {
		t.Errorf("group ops (second value) grants all: %v", e.Claims.Scp)
	}
	noneOf(t, e.Headers, "X-Forwarded-User", "X-Forwarded-Groups", "X-Forwarded-Email")
	// --prefer-email-to-user: the user is the email. Email is never the subject.
	rg.mustStatus(h("X-Forwarded-User", "Ben@Lab.org", "X-Forwarded-Email", "ben@lab.org"), 401)
}

func TestAuthentikUnsigned(t *testing.T) {
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "authentik", Grants: map[string][]string{"all": {"group:lab admins"}},
	}}, Options{})
	e := rg.mustSub(h("X-Authentik-Uid", "a1b2c3", "X-Authentik-Groups", "users|lab admins", "X-Authentik-Name", "Ben",
		"X-Authentik-Email", "ben@lab.org", "X-Authentik-Username", "ben"), "proxy:authentik#a1b2c3")
	if !hasScope(e, "operate") || e.Claims.Nm != "Ben" {
		t.Errorf("pipe-separated groups: %v, name %q", e.Claims.Scp, e.Claims.Nm)
	}
	noneOf(t, e.Headers, "X-Authentik-Uid", "X-Authentik-Groups", "X-Authentik-Name", "X-Authentik-Email", "X-Authentik-Username")
}

func TestCustomUnsigned(t *testing.T) {
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "custom", UserHeader: "X-Lab-User", GroupsHeader: "X-Lab-Groups", Separator: ";",
		Grants: map[string][]string{"all": {"group:b"}},
	}}, Options{})
	e := rg.mustSub(h("X-Lab-User", "ben", "X-Lab-Groups", "a; b"), "proxy:custom#ben")
	if !hasScope(e, "operate") {
		t.Errorf("groups: %v", e.Claims.Scp)
	}
	noneOf(t, e.Headers, "X-Lab-User", "X-Lab-Groups")
}

// Over TCP, identity headers from a peer outside `from:` are ignored: the
// request is the anonymous visitor.
func TestTCPUnvouchedPeerIgnored(t *testing.T) {
	rg := newRig(t, front.Config{Listen: "127.0.0.1:0", Proxy: &front.ProxyConfig{
		Preset: "authelia", From: front.StringList{"10.9.9.9"},
		Grants: map[string][]string{"all": {"ben"}},
	}}, Options{})
	rg.mustAnonymous(h("Remote-User", "ben", "Remote-Groups", "lab"))
	rg.mustAnonymous(h("Remote_User", "ben"))
}

// A loopback peer means every local process (F15): with no secret_file
// the shape is refused and the front falls back to local on a fresh
// loopback port, refusing (503) the address asked for (D-028).
func TestLoopbackWithoutSecretFallsBack(t *testing.T) {
	for _, from := range []front.StringList{{"127.0.0.1"}, {"127.0.0.0/8"}, {"::1"}, {"10.0.0.0/8", "127.0.0.1/32"}} {
		plan, client := front.ResolveWith(front.Config{Auth: "proxy", Listen: "0.0.0.0:8000", Proxy: &front.ProxyConfig{
			Preset: "authelia", From: from,
		}}, false, Factory(Options{LocalAddrs: noLocalAddrs}))
		if plan.Shape != front.ShapeLocal || client != nil || !strings.Contains(plan.Fallback, "secret_file") {
			t.Errorf("from %v: shape %q fallback %q, want local with a secret_file reason", from, plan.Shape, plan.Fallback)
		}
		if plan.Listen != "127.0.0.1:0" || plan.Refused != "0.0.0.0:8000" {
			t.Errorf("from %v: listen %q refused %q, want a fresh loopback port and 0.0.0.0:8000 refused", from, plan.Listen, plan.Refused)
		}
	}
}

// sec F1 (D-028, amended): a preset refused at start -- here a secret_file
// every user can read -- does not open the local shape on the address the
// proxy forwards to. A proxied request there is answered 503; the local
// console is on a fresh loopback port, and the runner sees nothing.
func TestRefusedPresetAnswers503WhereTheProxyPoints(t *testing.T) {
	secret := filepath.Join(t.TempDir(), "proxy-secret")
	if err := os.WriteFile(secret, []byte("s3cret-s3cret-s3cret\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	os.Chmod(secret, 0o644) // o+r whatever the umask
	ln0, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	requested := ln0.Addr().String()
	ln0.Close()
	plan, client := front.ResolveWith(front.Config{Auth: "proxy", Listen: requested, Proxy: &front.ProxyConfig{
		Preset: "authelia", From: front.StringList{"127.0.0.1"}, SecretFile: secret,
	}}, false, Factory(Options{}))
	t.Cleanup(plan.Close)
	if plan.Shape != front.ShapeLocal || client != nil || !strings.Contains(plan.Fallback, "readable by every user") {
		t.Fatalf("plan: shape %q fallback %q", plan.Shape, plan.Fallback)
	}
	rn := newRunner(t)
	f := front.New(front.Options{Plan: plan,
		Route: front.SingleRig(front.Rig{Name: "blender", Target: func(context.Context) (front.Target, error) {
			return front.Target{Endpoint: rn.ep, Aud: rn.aud, Key: rn.key}, nil
		}}),
		Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	t.Cleanup(f.Close)
	ln, err := front.Listen(plan)
	if err != nil {
		t.Fatal(err)
	}
	f.Bound(ln.Addr())
	srv := front.NewServer(plan, f)
	go srv.Serve(ln)
	t.Cleanup(func() { srv.Close() })

	send := func(addr, method string, hdr map[string][]string) (int, string) {
		t.Helper()
		req, _ := http.NewRequest(method, "http://"+addr+"/api/echo", strings.NewReader("{}"))
		for k, v := range hdr {
			req.Header[k] = v
		}
		resp, err := (&http.Client{Timeout: 10 * time.Second}).Do(req)
		if err != nil {
			t.Fatalf("%s: %v", addr, err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(b)
	}
	code, body := send(requested, "POST", h("Origin", "http://"+requested, "Content-Type", "application/json",
		"Remote-User", "mallory", SecretHeader, "s3cret-s3cret-s3cret", "X-Forwarded-For", "203.0.113.9"))
	if code != 503 || !strings.Contains(body, "misconfigured") || !strings.Contains(body, "readable by every user") {
		t.Fatalf("the address the proxy points at answered %d %q, want 503", code, body)
	}
	if strings.Contains(body, "s3cret") {
		t.Fatalf("the refusal leaks the secret: %q", body)
	}
	console := ln.Addr().String()
	if console == requested {
		t.Fatalf("the console is on the requested address %s", requested)
	}
	if code, body = send(console, "GET", nil); code != 200 || !strings.Contains(body, `"sub":"local:console"`) {
		t.Fatalf("console: %d %q", code, body)
	}
}

// A loopback peer with the shared secret: accepted only with the secret,
// compared in constant time; the secret never reaches the runner.
func TestLoopbackWithSecret(t *testing.T) {
	secret := filepath.Join(t.TempDir(), "proxy-secret")
	if err := os.WriteFile(secret, []byte("s3cret-s3cret-s3cret\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	rg := newRig(t, front.Config{Listen: "127.0.0.1:0", Proxy: &front.ProxyConfig{
		Preset: "authelia", From: front.StringList{"127.0.0.1"}, SecretFile: secret,
	}}, Options{})
	e := rg.mustSub(h("Remote-User", "ben", SecretHeader, "s3cret-s3cret-s3cret"), "proxy:authelia#ben")
	noneOf(t, e.Headers, SecretHeader, "Remote-User")
	// No secret: not vouched for, so ignored.
	rg.mustAnonymous(h("Remote-User", "ben"))
	// A wrong secret is a presented credential that fails: refused.
	rg.mustStatus(h("Remote-User", "ben", SecretHeader, "s3cret-s3cret-s3creT"), 401)
	rg.mustStatus(map[string][]string{"Remote-User": {"ben"}, SecretHeader: {"s3cret-s3cret-s3cret", "x"}}, 401)
}

// Unit: a vouched non-loopback TCP peer needs no secret; the peer is the
// TCP peer, never X-Forwarded-For.
func TestPeerAllowList(t *testing.T) {
	c, err := New(&front.ProxyConfig{Preset: "authelia", From: front.StringList{"10.1.0.0/16"}},
		front.Plan{Listen: "0.0.0.0:8000"}, Options{LocalAddrs: noLocalAddrs})
	if err != nil {
		t.Fatal(err)
	}
	req := func(peer string, kv ...string) *http.Request {
		r := httptest.NewRequest("GET", "/api/echo", nil)
		r.RemoteAddr = peer
		for i := 0; i+1 < len(kv); i += 2 {
			r.Header.Add(kv[i], kv[i+1])
		}
		return r
	}
	id, out, err := c.Authenticate(req("10.1.2.3:4000", "Remote-User", "ben"))
	if err != nil || out != front.Accept || id.Subject != "ben" || id.Issuer != "authelia" {
		t.Errorf("vouched peer: %+v %v %v", id, out, err)
	}
	if c.Name() != "proxy" {
		t.Errorf("Name %q", c.Name())
	}
	_, out, _ = c.Authenticate(req("10.2.0.1:4000", "Remote-User", "ben", "X-Forwarded-For", "10.1.2.3"))
	if out != front.NotMine {
		t.Errorf("unvouched peer claiming a vouched X-Forwarded-For: %v, want NotMine", out)
	}
	_, out, _ = c.Authenticate(req("[::ffff:10.1.2.3]:4000", "Remote-User", "ben"))
	if out != front.Accept {
		t.Errorf("IPv4-mapped vouched peer: %v", out)
	}
}

func noLocalAddrs() ([]netip.Addr, error) { return nil, nil }

// sec F3: a from: range wider than one host (/32, /128) lets every host in
// it assert any identity with no secret. It is allowed (a proxy whose
// address changes, in a container network, needs one) but warned of, once,
// at start; a host route, or a range with secret_file:, is not.
func TestWideFromRangeWarns(t *testing.T) {
	secret := filepath.Join(t.TempDir(), "proxy-secret")
	if err := os.WriteFile(secret, []byte("s3cret-s3cret-s3cret"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct {
		from   front.StringList
		secret string
		warn   string // "" = no warning
	}{
		{front.StringList{"10.0.0.0/8"}, "", "10.0.0.0/8"},
		{front.StringList{"10.9.9.9", "192.168.1.0/24", "fd00::/64"}, "", "192.168.1.0/24, fd00::/64"},
		{front.StringList{"10.9.9.8/31"}, "", "10.9.9.8/31"},
		{front.StringList{"10.9.9.9"}, "", ""},
		{front.StringList{"10.9.9.9/32", "fd00::5", "::ffff:10.9.9.7/128"}, "", ""},
		{front.StringList{"10.0.0.0/8"}, secret, ""},
	} {
		var buf strings.Builder
		log := slog.New(slog.NewTextHandler(&buf, nil))
		_, err := New(&front.ProxyConfig{Preset: "authelia", From: c.from, SecretFile: c.secret},
			front.Plan{Listen: "0.0.0.0:8000"}, Options{Logger: log, LocalAddrs: noLocalAddrs})
		if err != nil {
			t.Fatalf("from %v: %v", c.from, err)
		}
		out := buf.String()
		switch {
		case c.warn == "" && out != "":
			t.Errorf("from %v: warned %q, want nothing", c.from, out)
		case c.warn != "" && (strings.Count(out, "\n") != 1 || !strings.Contains(out, "level=WARN") || !strings.Contains(out, c.warn)):
			t.Errorf("from %v: logged %q, want one warning naming %s", c.from, out, c.warn)
		}
	}
}

// Shapes that cannot be vouched for are refused at start.
func TestUnsignedShapeRefusals(t *testing.T) {
	secret := filepath.Join(t.TempDir(), "s")
	os.WriteFile(secret, []byte("0123456789abcdef0123"), 0o600)
	short := filepath.Join(t.TempDir(), "short")
	os.WriteFile(short, []byte("abc"), 0o600)
	open := filepath.Join(t.TempDir(), "open")
	os.WriteFile(open, []byte("0123456789abcdef0123"), 0o644)
	os.Chmod(open, 0o644)
	bridge := func() ([]netip.Addr, error) { return []netip.Addr{netip.MustParseAddr("172.17.0.1")}, nil }

	cases := []struct {
		name   string
		c      front.ProxyConfig
		listen string
		local  func() ([]netip.Addr, error)
		want   string
	}{
		{"unix over tcp", front.ProxyConfig{Preset: "tailscale"}, "0.0.0.0:8000", nil, "unix"},
		{"cidr over unix", front.ProxyConfig{Preset: "authelia", From: front.StringList{"10.0.0.0/8"}}, "unix:/run/x.sock", nil, "unix"},
		{"every peer", front.ProxyConfig{Preset: "authelia", From: front.StringList{"0.0.0.0/0"}, SecretFile: secret}, "0.0.0.0:8000", noLocalAddrs, "every"},
		{"bridge gateway", front.ProxyConfig{Preset: "authelia", From: front.StringList{"172.17.0.0/16"}}, "0.0.0.0:8000", bridge, "secret_file"},
		{"bad cidr", front.ProxyConfig{Preset: "authelia", From: front.StringList{"lan"}}, "0.0.0.0:8000", noLocalAddrs, "lan"},
		{"missing secret file", front.ProxyConfig{Preset: "authelia", From: front.StringList{"127.0.0.1"}, SecretFile: "/nonexistent/s"}, "0.0.0.0:8000", noLocalAddrs, "secret_file"},
		{"short secret", front.ProxyConfig{Preset: "authelia", From: front.StringList{"127.0.0.1"}, SecretFile: short}, "0.0.0.0:8000", noLocalAddrs, "16"},
		{"world-readable secret", front.ProxyConfig{Preset: "authelia", From: front.StringList{"127.0.0.1"}, SecretFile: open}, "0.0.0.0:8000", noLocalAddrs, "readable"},
		{"custom without user_header", front.ProxyConfig{Preset: "custom", GroupsHeader: "X-G"}, "unix:/run/x.sock", nil, "user_header"},
		{"custom header is the front's", front.ProxyConfig{Preset: "custom", UserHeader: "Authorization"}, "unix:/run/x.sock", nil, "Authorization"},
		{"custom bad header name", front.ProxyConfig{Preset: "custom", UserHeader: "X User"}, "unix:/run/x.sock", nil, "header"},
		{"custom both", front.ProxyConfig{Preset: "custom", UserHeader: "X-U", JWT: &front.CustomJWT{}}, "unix:/run/x.sock", nil, "not both"},
		{"unknown preset", front.ProxyConfig{Preset: "keycloak"}, "unix:/run/x.sock", nil, "keycloak"},
		{"no preset", front.ProxyConfig{}, "unix:/run/x.sock", nil, "preset"},
		{"tailscale jwt keys", front.ProxyConfig{Preset: "tailscale", Issuer: "https://x"}, "unix:/run/x.sock", nil, "issuer"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			local := tc.local
			if local == nil {
				local = noLocalAddrs
			}
			_, err := New(&tc.c, front.Plan{Listen: tc.listen}, Options{LocalAddrs: local})
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Errorf("err %v, want one mentioning %q", err, tc.want)
			}
		})
	}
}
