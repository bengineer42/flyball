package frontwire

import (
	"path/filepath"
	"strings"
	"testing"

	"flyballd/internal/front"
)

func TestDecodeReadsEveryKey(t *testing.T) {
	c, err := Decode(map[string]any{
		"listen": "0.0.0.0:8443", "auth": "password", "url": "https://pi.lab:8443",
		"tls":      map[string]any{"cert": "/c.pem", "key": "/k.pem"},
		"password": "$scrypt$x", "anonymous": "read", "session": "2d",
		"trusted_proxies": []any{"10.0.0.1"},
		"proxy":           map[string]any{"preset": "authelia", "from": "unix", "grants": map[string]any{"all": []any{"alice"}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if c.Listen != "0.0.0.0:8443" || c.Auth != "password" || c.TLS == nil || c.TLS.Cert != "/c.pem" ||
		c.Anonymous != "read" || c.Session != "2d" || len(c.TrustedProxies) != 1 ||
		c.Proxy == nil || c.Proxy.Preset != "authelia" || len(c.Proxy.From) != 1 || c.Proxy.Grants["all"][0] != "alice" {
		t.Fatalf("decoded %+v", c)
	}
}

// Unknown keys and wrong types are errors (the Python model forbids extras).
func TestDecodeRefusesUnknownKeysAndBadTypes(t *testing.T) {
	for name, block := range map[string]map[string]any{
		"unknown": {"listen": "127.0.0.1:8000", "lisen": "x"},
		"type":    {"auth": []any{"local"}},
		"nested":  {"tls": map[string]any{"cert": "/c", "key": "/k", "chain": "/x"}},
	} {
		if _, err := Decode(block); err == nil {
			t.Errorf("%s: no error", name)
		}
	}
}

// A block that cannot be read serves the local shape on loopback, keeping
// the port asked for, with the reason in the banner (D-028).
func TestPlanBadConfigFallsBackToLoopback(t *testing.T) {
	c := front.Config{Listen: "0.0.0.0:18410"}
	p, client := Plan(c, errBad("auth: not a string"), false)
	defer p.Close()
	if client != nil || p.Shape != front.ShapeLocal || p.Listen != "127.0.0.1:18410" || p.Requested != "0.0.0.0:18410" {
		t.Fatalf("plan = %+v", p)
	}
	if !strings.Contains(p.Banner(), "auth: not a string") || !strings.Contains(p.Banner(), "D-028") {
		t.Fatalf("banner = %q", p.Banner())
	}
}

// A good block is ResolveWith's plan, with the hook's factory: nil here,
// so a proxy shape falls back.
func TestPlanUsesTheProxyFactoryHook(t *testing.T) {
	c := front.Config{Listen: "127.0.0.1:18411", Auth: "proxy", Proxy: &front.ProxyConfig{Preset: "authelia"}}
	p, _ := Plan(c, nil, false)
	if p.Fallback == "" {
		t.Fatalf("with no factory the proxy shape must fall back: %+v", p)
	}
	called := false
	old := ProxyFactory
	ProxyFactory = func(*front.ProxyConfig, front.Plan) (front.Client, error) { called = true; return nil, errBad("no") }
	defer func() { ProxyFactory = old }()
	Plan(c, nil, false)
	if !called {
		t.Fatal("Plan did not call ProxyFactory")
	}
}

func TestStateDirs(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", "/xdg/state")
	if got := RunDir("abcd1234"); got != "/xdg/state/flyball/front-abcd1234" {
		t.Fatalf("RunDir = %q", got)
	}
	t.Setenv("XDG_STATE_HOME", "")
	t.Setenv("HOME", "/home/u")
	if got := RunDir("abcd1234"); got != "/home/u/.local/state/flyball/front-abcd1234" {
		t.Fatalf("RunDir = %q", got)
	}
	got := DaemonDir("data")
	if !filepath.IsAbs(got) || filepath.Base(got) != "front" {
		t.Fatalf("DaemonDir = %q", got)
	}
}

func TestInsecureOpenEnv(t *testing.T) {
	for v, want := range map[string]bool{"1": true, "yes": true, "TRUE": true, "": false, "0": false} {
		t.Setenv("FLYBALL_INSECURE_OPEN", v)
		if InsecureOpenEnv() != want {
			t.Errorf("FLYBALL_INSECURE_OPEN=%q: %v", v, !want)
		}
	}
}

type errBad string

func (e errBad) Error() string { return string(e) }
