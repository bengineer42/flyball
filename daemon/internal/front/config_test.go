package front

import (
	"errors"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

func TestResolveDefault(t *testing.T) {
	p := Resolve(Config{}, false)
	defer p.Close()
	if p.Shape != "local" || p.Listen != "127.0.0.1:8000" || p.Fallback != "" || len(p.Warnings) != 0 {
		t.Fatalf("default plan: %+v", p)
	}
	if !slices.Equal(p.HostAllow, []string{"localhost", "127.0.0.1", "[::1]"}) {
		t.Fatalf("local HostAllow = %v", p.HostAllow)
	}
	if p.Secure || p.Anonymous != "none" || p.SessionIdle != 0 {
		t.Fatalf("default plan: %+v", p)
	}
}

// Merge requirement 24 (the front's half): every auth misconfiguration
// removes exposure, never operation -- the plan serves the local shape on
// loopback with a banner, and never fails.
func TestResolveFallbacks(t *testing.T) {
	dir := t.TempDir()
	cases := map[string]Config{
		"plaintext password": {Listen: "0.0.0.0:9000", Auth: "password", Password: "change-me"},
		"missing password":   {Listen: "0.0.0.0:9000", Auth: "password"},
		"sso":                {Listen: "0.0.0.0:9000", Auth: "sso"},
		"unknown shape":      {Listen: "0.0.0.0:9000", Auth: "ldap"},
		"bad TLS files": {Listen: "0.0.0.0:9000", Auth: "password", Password: testScrypt,
			TLS: &TLSFiles{Cert: filepath.Join(dir, "no.pem"), Key: filepath.Join(dir, "no.key")}},
		"non-loopback local": {Listen: "0.0.0.0:9000"},
		"proxy without block": {Listen: "0.0.0.0:9000", Auth: "proxy"},
		"proxy, no presets":   {Listen: "0.0.0.0:9000", Auth: "proxy", Proxy: &ProxyConfig{Preset: "authelia"}},
		"bad url":             {Listen: "0.0.0.0:9000", Auth: "password", Password: testScrypt, URL: "ftp://x"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			p := Resolve(c, false)
			defer p.Close()
			if p.Shape != "local" || p.Listen != "127.0.0.1:9000" || p.Fallback == "" {
				t.Fatalf("plan: shape %q listen %q fallback %q", p.Shape, p.Listen, p.Fallback)
			}
			if p.TLS != nil || p.Secure {
				t.Fatal("a fallback kept TLS")
			}
			if !strings.Contains(p.Banner(), p.Fallback) || !strings.Contains(p.Banner(), "127.0.0.1:9000") {
				t.Fatalf("banner %q", p.Banner())
			}
		})
	}
}

func TestResolveProxyFactory(t *testing.T) {
	c := Config{Listen: "0.0.0.0:9000", Auth: "proxy", Proxy: &ProxyConfig{Preset: "authelia"}}
	refuse := func(*ProxyConfig, Plan) (Client, error) { return nil, errors.New("unvouched peer") }
	p, client := ResolveWith(c, false, refuse)
	if p.Shape != "local" || !strings.Contains(p.Fallback, "unvouched peer") || client != nil {
		t.Fatalf("refused preset: %+v", p)
	}
	accept := func(pc *ProxyConfig, pl Plan) (Client, error) {
		if pc.Preset != "authelia" || pl.Listen != "0.0.0.0:9000" {
			t.Errorf("factory got %+v, %+v", pc, pl)
		}
		return stubClient{}, nil
	}
	p, client = ResolveWith(c, false, accept)
	if p.Shape != "proxy" || p.Fallback != "" || client == nil || p.HostAllow != nil {
		t.Fatalf("accepted preset: %+v", p)
	}
}

func TestResolveLocalInsecureOpen(t *testing.T) {
	p := Resolve(Config{Listen: "0.0.0.0:9000"}, true)
	if p.Shape != "local" || p.Listen != "0.0.0.0:9000" || p.Fallback != "" || p.HostAllow != nil {
		t.Fatalf("plan: %+v", p)
	}
	if len(p.Warnings) != 1 || !strings.Contains(p.Warnings[0], "OPEN") {
		t.Fatalf("warnings: %v", p.Warnings)
	}
}

// Merge requirement 19: a non-loopback listen with credentials and no TLS
// warns of cleartext at start.
func TestResolveCleartextWarning(t *testing.T) {
	p := Resolve(Config{Listen: "0.0.0.0:9000", Auth: "password", Password: testScrypt}, false)
	if p.Shape != "password" || p.Fallback != "" || p.HostAllow != nil {
		t.Fatalf("plan: %+v", p)
	}
	if len(p.Warnings) != 1 || !strings.Contains(p.Warnings[0], "plain HTTP") {
		t.Fatalf("warnings: %v", p.Warnings)
	}
	p = Resolve(Config{Listen: "127.0.0.1:9000", Auth: "password", Password: testScrypt}, false)
	if len(p.Warnings) != 0 {
		t.Fatalf("loopback warned: %v", p.Warnings)
	}
	dir := t.TempDir()
	cert, key := filepath.Join(dir, "c.pem"), filepath.Join(dir, "k.pem")
	writeCert(t, cert, key)
	p = Resolve(Config{Listen: "0.0.0.0:9000", Auth: "password", Password: testScrypt, TLS: &TLSFiles{Cert: cert, Key: key}}, false)
	defer p.Close()
	if len(p.Warnings) != 0 || p.TLS == nil || !p.Secure {
		t.Fatalf("TLS plan: %+v", p)
	}
}

func TestResolveURL(t *testing.T) {
	p := Resolve(Config{Listen: "0.0.0.0:9000", Auth: "password", Password: testScrypt, URL: "https://Pi.Lab:8443/"}, false)
	if !p.Secure {
		t.Fatal("an https url: did not set Secure")
	}
	if !slices.Equal(p.HostAllow, []string{"pi.lab:8443", "localhost", "127.0.0.1", "[::1]"}) {
		t.Fatalf("HostAllow = %v", p.HostAllow)
	}
	if !slices.Equal(p.OriginAllow, []string{"https://pi.lab:8443"}) {
		t.Fatalf("OriginAllow = %v", p.OriginAllow)
	}
	p = Resolve(Config{URL: "http://pi.lab"}, false)
	if p.Secure || !slices.Equal(p.OriginAllow, []string{"http://pi.lab"}) || !slices.Contains(p.HostAllow, "pi.lab:80") {
		t.Fatalf("http url: %+v", p)
	}
}

func TestResolveReferenceKeys(t *testing.T) {
	p := Resolve(Config{Auth: "password", Password: testScrypt, Anonymous: "read", Session: "2d",
		TrustedProxies: []string{"10.0.0.0/8", "192.168.1.5", "nonsense"}}, false)
	if p.Anonymous != "read" || p.SessionIdle != 48*time.Hour {
		t.Fatalf("plan: %+v", p)
	}
	if len(p.Trusted) != 2 || len(p.Warnings) != 1 || !strings.Contains(p.Warnings[0], "nonsense") {
		t.Fatalf("trusted %v warnings %v", p.Trusted, p.Warnings)
	}
	p = Resolve(Config{Auth: "password", Password: testScrypt, Anonymous: "write", Session: "soon"}, false)
	if p.Anonymous != "none" || p.SessionIdle != 0 || len(p.Warnings) != 2 {
		t.Fatalf("bad reference keys: %+v", p)
	}
}

func TestResolveUnixListen(t *testing.T) {
	p := Resolve(Config{Listen: "unix:/run/flyball/front.sock"}, false)
	if p.Shape != "local" || p.Listen != "unix:/run/flyball/front.sock" || p.Fallback != "" {
		t.Fatalf("plan: %+v", p)
	}
}
