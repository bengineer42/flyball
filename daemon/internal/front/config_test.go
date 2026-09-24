package front

import (
	"errors"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"flyballd/internal/front/store"
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
// loopback with a banner, and never fails. A credential shape's fallback
// serves it on a fresh loopback address and refuses (503) the one asked
// for (D-028, amended: sec F1); local beyond loopback keeps its port.
func TestResolveFallbacks(t *testing.T) {
	dir := t.TempDir()
	cases := map[string]Config{
		"plaintext password": {Listen: "0.0.0.0:9000", Auth: "password", Password: "change-me"},
		"missing password":   {Listen: "0.0.0.0:9000", Auth: "password"},
		"sso":                {Listen: "0.0.0.0:9000", Auth: "sso"},
		"unknown shape":      {Listen: "0.0.0.0:9000", Auth: "ldap"},
		"bad TLS files": {Listen: "0.0.0.0:9000", Auth: "password", Password: testScrypt,
			TLS: &TLSFiles{Cert: filepath.Join(dir, "no.pem"), Key: filepath.Join(dir, "no.key")}},
		"non-loopback local":  {Listen: "0.0.0.0:9000"},
		"proxy without block": {Listen: "0.0.0.0:9000", Auth: "proxy"},
		"proxy, no presets":   {Listen: "0.0.0.0:9000", Auth: "proxy", Proxy: &ProxyConfig{Preset: "authelia"}},
		"bad url":             {Listen: "0.0.0.0:9000", Auth: "password", Password: testScrypt, URL: "ftp://x"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			p := Resolve(c, false)
			defer p.Close()
			listen, refused := "127.0.0.1:0", "0.0.0.0:9000"
			if c.Auth == "" {
				listen, refused = "127.0.0.1:9000", ""
			}
			if p.Shape != "local" || p.Listen != listen || p.Refused != refused || p.Fallback == "" {
				t.Fatalf("plan: shape %q listen %q refused %q fallback %q", p.Shape, p.Listen, p.Refused, p.Fallback)
			}
			if p.TLS != nil || p.Secure {
				t.Fatal("a fallback kept TLS")
			}
			if !strings.Contains(p.Banner(), p.Fallback) || !strings.Contains(p.Banner(), listen) {
				t.Fatalf("banner %q", p.Banner())
			}
			if refused != "" && !strings.Contains(p.Banner(), refused+" answers 503") {
				t.Fatalf("banner %q does not say the requested address answers 503", p.Banner())
			}
		})
	}
	// Beside a unix socket, the console is a fresh socket in the same
	// directory; one whose path would be too long gets a loopback port.
	p := Resolve(Config{Listen: "unix:/run/flyball/front.sock", Auth: "sso"}, false)
	if p.Listen != "unix:/run/flyball/front.sock.local" || p.Refused != "unix:/run/flyball/front.sock" {
		t.Fatalf("unix fallback: listen %q refused %q", p.Listen, p.Refused)
	}
	long := "unix:/" + strings.Repeat("d", 96) + ".sock"
	if p = Resolve(Config{Listen: long, Auth: "sso"}, false); p.Listen != "127.0.0.1:0" || p.Refused != long {
		t.Fatalf("long unix fallback: listen %q refused %q", p.Listen, p.Refused)
	}
	// A listen that is not an address: nothing to refuse.
	if p = Resolve(Config{Listen: "nowhere", Auth: "sso"}, false); p.Listen != DefaultListen || p.Refused != "" {
		t.Fatalf("bad listen: listen %q refused %q", p.Listen, p.Refused)
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
	// Not any Host: loopback names, an IP address and the machine's own
	// names (HostKnown), and url:'s host when set (D-043).
	if p.Shape != "local" || p.Listen != "0.0.0.0:9000" || p.Fallback != "" || !p.HostKnown ||
		!slices.Equal(p.HostAllow, []string{"localhost", "127.0.0.1", "[::1]"}) {
		t.Fatalf("plan: %+v", p)
	}
	if u := Resolve(Config{Listen: "0.0.0.0:9000", URL: "http://pi.lab:9000"}, true); !u.HostKnown ||
		!slices.Equal(u.HostAllow, []string{"pi.lab:9000", "localhost", "127.0.0.1", "[::1]"}) {
		t.Fatalf("url: plan: %+v", u)
	}
	if l := Resolve(Config{}, false); l.HostKnown {
		t.Fatalf("the loopback local shape takes loopback names only: %+v", l)
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
	p := Resolve(Config{Auth: "password", Password: testScrypt, Anonymous: "read", Login: "2d",
		TrustedProxies: []string{"10.0.0.0/8", "192.168.1.5", "nonsense"}}, false)
	if p.Anonymous != "read" || p.SessionIdle != 48*time.Hour {
		t.Fatalf("plan: %+v", p)
	}
	if len(p.Trusted) != 2 || len(p.Warnings) != 1 || !strings.Contains(p.Warnings[0], "nonsense") {
		t.Fatalf("trusted %v warnings %v", p.Trusted, p.Warnings)
	}
	p = Resolve(Config{Auth: "password", Password: testScrypt, Anonymous: "write", Login: "soon"}, false)
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

func TestResolveTokensUnsetUsesBuiltins(t *testing.T) {
	p := Resolve(Config{}, false)
	if p.Lifetimes != store.DefaultLifetimes() {
		t.Fatalf("Lifetimes = %+v, want the built-ins %+v", p.Lifetimes, store.DefaultLifetimes())
	}
	if len(p.Warnings) != 0 {
		t.Fatalf("warnings = %v, want none", p.Warnings)
	}
}

func TestResolveTokensTightenMax(t *testing.T) {
	p := Resolve(Config{Tokens: &TokensConfig{DefaultLifetime: "30d", MaxLifetime: "60d"}}, false)
	if p.Lifetimes.Default != 30*24*time.Hour || p.Lifetimes.Max != 60*24*time.Hour {
		t.Fatalf("Lifetimes = %+v", p.Lifetimes)
	}
	if len(p.Warnings) != 0 {
		t.Fatalf("warnings = %v, want none", p.Warnings)
	}
}

func TestResolveTokensMaxAboveBuiltinFallsBackWithWarning(t *testing.T) {
	p := Resolve(Config{Tokens: &TokensConfig{MaxLifetime: "400d"}}, false)
	if p.Lifetimes.Max != store.TokenLifetimeMax {
		t.Fatalf("max = %v, want the built-in ceiling %v", p.Lifetimes.Max, store.TokenLifetimeMax)
	}
	if len(p.Warnings) != 1 || !strings.Contains(p.Warnings[0], "max_lifetime") {
		t.Fatalf("warnings = %v, want one naming max_lifetime", p.Warnings)
	}
}

func TestResolveTokensDefaultAboveMaxFallsBack(t *testing.T) {
	p := Resolve(Config{Tokens: &TokensConfig{DefaultLifetime: "100d", MaxLifetime: "50d"}}, false)
	if p.Lifetimes.Default != store.TokenLifetimeDefault {
		t.Fatalf("default = %v, want the built-in %v", p.Lifetimes.Default, store.TokenLifetimeDefault)
	}
	if p.Lifetimes.Max != 50*24*time.Hour {
		t.Fatalf("max = %v, want 50d", p.Lifetimes.Max)
	}
	if len(p.Warnings) != 1 || !strings.Contains(p.Warnings[0], "default_lifetime") {
		t.Fatalf("warnings = %v, want one naming default_lifetime", p.Warnings)
	}
	if !strings.Contains(p.Banner(), "default_lifetime") {
		t.Fatalf("Banner() = %q, want the tokens warning", p.Banner())
	}
}
