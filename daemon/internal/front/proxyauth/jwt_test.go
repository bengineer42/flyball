package proxyauth

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"

	"flyballd/internal/front"

	jose "github.com/go-jose/go-jose/v4"
)

// The JWT presets run against a real JWKS served over TLS by httptest,
// with real RS256 and ES256 tokens.

const cfIssuer = "https://lab.cloudflareaccess.com"

func cloudflareRig(t *testing.T, p *idp, o Options) *rig {
	o.HTTPClient = p.anyHostClient()
	return newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "cloudflare", Team: "lab", Audience: "aud-tag-1",
		Grants: map[string][]string{"all": {"u-admin"}},
	}}, o)
}

func TestCloudflare(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("k1", rsaKey, jose.RS256)
	p.add("e1", ecKey, jose.ES256)
	rg := cloudflareRig(t, p, Options{})
	now := time.Now()
	hdr := func(tok string) map[string][]string { return h("Cf-Access-Jwt-Assertion", tok) }

	e := rg.mustSub(hdr(sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, []string{"aud-tag-1"}, "u-1", now,
		"email", "u-admin", "name", "Ben"))), "proxy:"+cfIssuer+"#u-1")
	if hasScope(e, "operate") {
		t.Errorf("an email claim naming a granted subject granted it: %v", e.Claims.Scp)
	}
	noneOf(t, e.Headers, "Cf-Access-Jwt-Assertion", "Cf-Access-Authenticated-User-Email")
	e = rg.mustSub(hdr(sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-admin", now))), "proxy:"+cfIssuer+"#u-admin")
	if !hasScope(e, "operate") {
		t.Errorf("a granted subject: %v", e.Claims.Scp)
	}

	for name, tok := range map[string]string{
		"ES256 (not on cloudflare's list)": sign(t, ecKey, jose.ES256, "e1", claims(cfIssuer, "aud-tag-1", "u-1", now)),
		"HS256":                            sign(t, []byte("0123456789abcdef0123456789abcdef"), jose.HS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now)),
		"none":                             unsigned(claims(cfIssuer, "aud-tag-1", "u-1", now)),
		"wrong iss":                        sign(t, rsaKey, jose.RS256, "k1", claims("https://evil.cloudflareaccess.com", "aud-tag-1", "u-1", now)),
		"wrong aud":                        sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-2", "u-1", now)),
		"expired":                          sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now, "exp", now.Add(-2*time.Minute).Unix())),
		"not yet valid":                    sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now, "nbf", now.Add(5*time.Minute).Unix())),
		"no exp":                           sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now, "exp", nil)),
		"no sub":                           sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "", now)),
		"signed by another key":            sign(t, rsaKey2, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now)),
		"tampered":                         tamper(sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now))),
		"garbage":                          "not.a.jwt",
	} {
		t.Run(name, func(t *testing.T) { rg.mustStatus(hdr(tok), 401) })
	}
	// Two assertions: ambiguous.
	good := sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now))
	rg.mustStatus(map[string][]string{"Cf-Access-Jwt-Assertion": {good, good}}, 401)
	// Within the 60 s skew: accepted.
	rg.mustSub(hdr(sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", now, "exp", now.Add(-30*time.Second).Unix()))), "proxy:"+cfIssuer+"#u-1")
}

// tamper swaps the payload's sub without re-signing.
func tamper(tok string) string {
	parts := strings.Split(tok, ".")
	parts[1] = unsigned(map[string]any{"iss": cfIssuer, "aud": "aud-tag-1", "sub": "u-admin", "exp": time.Now().Add(time.Hour).Unix()})
	parts[1] = strings.Split(parts[1], ".")[1]
	return strings.Join(parts, ".")
}

// An unknown kid refetches the JWKS once; another within 60 s does not.
func TestUnknownKidRefetchesOnce(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("k1", rsaKey, jose.RS256)
	clk := &clock{t: time.Now()}
	rg := cloudflareRig(t, p, Options{Now: clk.now})
	tok := func(key any, kid string) map[string][]string {
		return h("Cf-Access-Jwt-Assertion", sign(t, key, jose.RS256, kid, claims(cfIssuer, "aud-tag-1", "u-1", clk.now())))
	}
	rg.mustSub(tok(rsaKey, "k1"), "proxy:"+cfIssuer+"#u-1")
	rg.mustSub(tok(rsaKey, "k1"), "proxy:"+cfIssuer+"#u-1")
	if n := p.jwksHits(); n != 1 {
		t.Fatalf("%d JWKS fetches after two tokens, want 1 (cached)", n)
	}
	// The IdP rotates in k2: the first k2 token refetches and is accepted.
	p.add("k2", rsaKey2, jose.RS256)
	rg.mustSub(tok(rsaKey2, "k2"), "proxy:"+cfIssuer+"#u-1")
	if n := p.jwksHits(); n != 2 {
		t.Fatalf("%d JWKS fetches after an unknown kid, want 2", n)
	}
	// k3 is nowhere: refused, and no second refetch within 60 s.
	rg.mustStatus(tok(rsaKey2, "k3"), 401)
	rg.mustStatus(tok(rsaKey2, "k3"), 401)
	if n := p.jwksHits(); n != 2 {
		t.Fatalf("%d JWKS fetches, want still 2 (at most one refetch per 60 s)", n)
	}
	clk.add(61 * time.Second)
	rg.mustStatus(tok(rsaKey2, "k3"), 401)
	if n := p.jwksHits(); n != 3 {
		t.Fatalf("%d JWKS fetches after 61 s, want 3", n)
	}
}

// sec F4: a JWKS fetch runs outside the key set's lock. While requests for
// an unknown kid wait on a slow IdP, a token whose key is cached is
// verified at once; the waiting requests share one fetch.
func TestSlowJWKSDoesNotStallCachedKeys(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("k1", rsaKey, jose.RS256)
	rg := cloudflareRig(t, p, Options{})
	tok := func(key any, kid string) map[string][]string {
		return h("Cf-Access-Jwt-Assertion", sign(t, key, jose.RS256, kid, claims(cfIssuer, "aud-tag-1", "u-1", time.Now())))
	}
	rg.mustSub(tok(rsaKey, "k1"), "proxy:"+cfIssuer+"#u-1") // k1 cached
	const slow = 3 * time.Second
	p.setDelay(slow)
	p.add("k2", rsaKey2, jose.RS256)
	var wg sync.WaitGroup
	codes := make([]int, 3)
	for i := range codes {
		wg.Add(1)
		go func() {
			defer wg.Done()
			codes[i], _ = rg.get(tok(rsaKey2, "k2"))
		}()
	}
	time.Sleep(300 * time.Millisecond) // the k2 refetch is under way
	start := time.Now()
	rg.mustSub(tok(rsaKey, "k1"), "proxy:"+cfIssuer+"#u-1")
	if took := time.Since(start); took > slow/2 {
		t.Errorf("a token with a cached key took %s: it waited behind the JWKS fetch", took.Round(time.Millisecond))
	}
	wg.Wait()
	for i, c := range codes {
		if c != 200 {
			t.Errorf("k2 request %d: %d, want 200 once the refetch brought k2", i, c)
		}
	}
	if n := p.jwksHits(); n != 2 {
		t.Errorf("%d JWKS fetches, want 2 (the first, and one refetch shared by the k2 requests)", n)
	}
}

// A JWKS that cannot be fetched is 503, never anonymous (F10), even with
// anonymous: read.
func TestJWKSUnreachableIs503(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("k1", rsaKey, jose.RS256)
	p.setFail(true)
	rg := cloudflareRig(t, p, Options{})
	tok := sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", time.Now()))
	rg.mustStatus(h("Cf-Access-Jwt-Assertion", tok), 503)
	rg.mustAnonymous(nil) // no assertion: anonymous as configured
	// The IdP gone entirely.
	p.srv.Close()
	rg2 := cloudflareRig(t, p, Options{})
	rg2.mustStatus(h("Cf-Access-Jwt-Assertion", tok), 503)
}

func TestPomerium(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("p1", ecKey, jose.ES256)
	host := strings.TrimPrefix(p.srv.URL, "https://")
	now := time.Now()
	rg := newRig(t, front.Config{Listen: unixListen(t), URL: p.srv.URL, Proxy: &front.ProxyConfig{
		Preset: "pomerium", Grants: map[string][]string{"all": {"group:g-lab"}},
	}}, Options{HTTPClient: p.client()})
	e := rg.mustSub(h("X-Pomerium-Jwt-Assertion", sign(t, ecKey, jose.ES256, "p1", claims(host, host, "p-123", now, "groups", []string{"g-lab"}, "email", "ben@lab.org"))),
		"proxy:"+host+"#p-123")
	if !hasScope(e, "operate") {
		t.Errorf("groups claim: %v", e.Claims.Scp)
	}
	noneOf(t, e.Headers, "X-Pomerium-Jwt-Assertion")
	rg.mustStatus(h("X-Pomerium-Jwt-Assertion", sign(t, rsaKey, jose.RS256, "p1", claims(host, host, "p-123", now))), 401)
	rg.mustStatus(h("X-Pomerium-Jwt-Assertion", sign(t, ecKey, jose.ES256, "p1", claims("other.example", host, "p-123", now))), 401)
	// X-Pomerium-Claim-* is never trusted.
	rg.mustAnonymous(h("X-Pomerium-Claim-Sub", "p-123", "X-Pomerium-Claim-Groups", "g-lab"))

	// iss and aud are [Unverified]: configurable.
	rg2 := newRig(t, front.Config{Listen: unixListen(t), URL: p.srv.URL, Proxy: &front.ProxyConfig{
		Preset: "pomerium", Issuer: "authenticate.lab.org", Audience: "flyball.lab.org",
	}}, Options{HTTPClient: p.client()})
	rg2.mustSub(h("X-Pomerium-Jwt-Assertion", sign(t, ecKey, jose.ES256, "p1", claims("authenticate.lab.org", []string{"flyball.lab.org"}, "p-123", now))),
		"proxy:authenticate.lab.org#p-123")
}

func TestAuthentikSigned(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("a1", rsaKey, jose.RS256)
	iss := p.srv.URL + "/application/o/flyball/"
	now := time.Now()
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "authentik", Issuer: iss, Grants: map[string][]string{"all": {"group:lab"}},
	}}, Options{HTTPClient: p.client()})
	e := rg.mustSub(h("X-Authentik-Jwt", sign(t, rsaKey, jose.RS256, "a1", claims(iss, "client-id", "hashed-sub", now, "groups", []string{"lab"}))),
		"proxy:"+iss+"#hashed-sub")
	if !hasScope(e, "operate") {
		t.Errorf("groups: %v", e.Claims.Scp)
	}
	// authentik with no signing key signs HS256 with the client secret:
	// refused, always.
	rg.mustStatus(h("X-Authentik-Jwt", sign(t, []byte("client-secret-client-secret-0123"), jose.HS256, "", claims(iss, "client-id", "hashed-sub", now))), 401)
	// Unsigned identity headers are ignored in the signed mode.
	rg.mustAnonymous(h("X-Authentik-Uid", "a1b2c3"))
}

func TestOAuth2ProxySigned(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("o1", ecKey, jose.ES256)
	now := time.Now()
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "oauth2-proxy", Issuer: p.srv.URL, Audience: "flyball",
		Grants: map[string][]string{"all": {"group:ops"}},
	}}, Options{HTTPClient: p.client()})
	e := rg.mustSub(h("Authorization", "Bearer "+sign(t, ecKey, jose.ES256, "o1", claims(p.srv.URL, "flyball", "0af3", now, "groups", []string{"ops"}, "email", "ben@lab.org"))),
		"proxy:"+p.srv.URL+"#0af3")
	if !hasScope(e, "operate") {
		t.Errorf("groups: %v", e.Claims.Scp)
	}
	noneOf(t, e.Headers, "Authorization")
	rg.mustStatus(h("Authorization", "Bearer "+sign(t, ecKey, jose.ES256, "o1", claims(p.srv.URL, "other-client", "0af3", now))), 401)
	// Unsigned headers are ignored in the signed mode.
	rg.mustAnonymous(h("X-Forwarded-User", "ben"))
}

func TestCustomJWT(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("c1", ecKey, jose.ES256)
	now := time.Now()
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "custom", JWT: &front.CustomJWT{Header: "X-Lab-Jwt", JWKSURL: p.srv.URL + "/keys", Issuer: "lab-idp", Audience: "flyball", Algorithms: []string{"ES256"}},
	}}, Options{HTTPClient: p.client()})
	rg.mustSub(h("X-Lab-Jwt", sign(t, ecKey, jose.ES256, "c1", claims("lab-idp", "flyball", "s-1", now))), "proxy:lab-idp#s-1")
	rg.mustStatus(h("X-Lab-Jwt", sign(t, rsaKey, jose.RS256, "c1", claims("lab-idp", "flyball", "s-1", now))), 401)
}

// A JWK that names its alg verifies only that alg, even when the preset
// allows another the same key could produce (RS256 key, PS256 token).
func TestJWKAlgPinned(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("r1", rsaKey, jose.RS256)
	now := time.Now()
	rg := newRig(t, front.Config{Listen: unixListen(t), Proxy: &front.ProxyConfig{
		Preset: "custom", JWT: &front.CustomJWT{Header: "X-Lab-Jwt", JWKSURL: p.srv.URL + "/keys", Issuer: "lab-idp", Audience: "flyball", Algorithms: []string{"RS256", "PS256"}},
	}}, Options{HTTPClient: p.client()})
	rg.mustSub(h("X-Lab-Jwt", sign(t, rsaKey, jose.RS256, "r1", claims("lab-idp", "flyball", "s-1", now))), "proxy:lab-idp#s-1")
	rg.mustStatus(h("X-Lab-Jwt", sign(t, rsaKey, jose.PS256, "r1", claims("lab-idp", "flyball", "s-1", now))), 401)
}

// Signed-mode configuration errors refuse the shape.
func TestSignedShapeRefusals(t *testing.T) {
	u, _ := url.Parse("https://flyball.lab.org")
	plain, _ := url.Parse("http://flyball.lab.org")
	jwt := func(algs ...string) *front.CustomJWT {
		return &front.CustomJWT{Header: "X-Jwt", JWKSURL: "https://idp.lab.org/keys", Issuer: "i", Audience: "a", Algorithms: algs}
	}
	cases := []struct {
		name string
		c    front.ProxyConfig
		url  *url.URL
		want string
	}{
		{"custom HS256", front.ProxyConfig{Preset: "custom", JWT: jwt("HS256")}, nil, "HS256"},
		{"custom HS512 among others", front.ProxyConfig{Preset: "custom", JWT: jwt("RS256", "HS512")}, nil, "HS512"},
		{"custom none", front.ProxyConfig{Preset: "custom", JWT: jwt("none")}, nil, "none"},
		{"custom no algorithms", front.ProxyConfig{Preset: "custom", JWT: jwt()}, nil, "algorithms"},
		{"custom unknown alg", front.ProxyConfig{Preset: "custom", JWT: jwt("RS1")}, nil, "RS1"},
		{"custom no audience", front.ProxyConfig{Preset: "custom", JWT: &front.CustomJWT{Header: "X-Jwt", JWKSURL: "https://i/k", Issuer: "i", Algorithms: []string{"RS256"}}}, nil, "audience"},
		{"custom http jwks", front.ProxyConfig{Preset: "custom", JWT: &front.CustomJWT{Header: "X-Jwt", JWKSURL: "http://idp.lab.org/k", Issuer: "i", Audience: "a", Algorithms: []string{"RS256"}}}, nil, "https"},
		{"custom header is the front's", front.ProxyConfig{Preset: "custom", JWT: &front.CustomJWT{Header: "Cookie", JWKSURL: "https://i/k", Issuer: "i", Audience: "a", Algorithms: []string{"RS256"}}}, nil, "Cookie"},
		{"pomerium without url", front.ProxyConfig{Preset: "pomerium"}, nil, "url"},
		{"pomerium over http", front.ProxyConfig{Preset: "pomerium"}, plain, "https"},
		{"cloudflare without team", front.ProxyConfig{Preset: "cloudflare", Audience: "a"}, nil, "team"},
		{"cloudflare without audience", front.ProxyConfig{Preset: "cloudflare", Team: "lab"}, nil, "audience"},
		{"cloudflare bad team", front.ProxyConfig{Preset: "cloudflare", Team: "evil.com/x?", Audience: "a"}, nil, "team"},
		{"oauth2-proxy signed without audience", front.ProxyConfig{Preset: "oauth2-proxy", Issuer: "https://idp.lab.org"}, nil, "audience"},
		{"oauth2-proxy both modes", front.ProxyConfig{Preset: "oauth2-proxy", Issuer: "https://idp.lab.org", Audience: "a", From: front.StringList{"unix"}}, nil, "not both"},
		{"authentik both modes", front.ProxyConfig{Preset: "authentik", Issuer: "https://idp.lab.org/application/o/x/", From: front.StringList{"unix"}}, nil, "not both"},
		{"authentik http issuer", front.ProxyConfig{Preset: "authentik", Issuer: "http://idp.lab.org/application/o/x/"}, nil, "https"},
		{"cloudflare with from", front.ProxyConfig{Preset: "cloudflare", Team: "lab", Audience: "a", From: front.StringList{"unix"}}, nil, "from"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := New(&tc.c, front.Plan{Listen: "unix:/run/x.sock", URL: tc.url}, Options{LocalAddrs: noLocalAddrs})
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Errorf("err %v, want one mentioning %q", err, tc.want)
			}
		})
	}
	if _, err := New(&front.ProxyConfig{Preset: "pomerium"}, front.Plan{Listen: "unix:/run/x.sock", URL: u}, Options{}); err != nil {
		t.Errorf("pomerium with an https url: %v", err)
	}
}

// Construction makes no network call: an IdP that is down at start is a
// 503 at the first request, not a refused shape.
func TestNoFetchAtStart(t *testing.T) {
	calls := 0
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls++ }))
	defer srv.Close()
	if _, err := New(&front.ProxyConfig{Preset: "oauth2-proxy", Issuer: srv.URL, Audience: "a"}, front.Plan{Listen: "unix:/run/x.sock"}, Options{HTTPClient: srv.Client()}); err != nil {
		t.Fatal(err)
	}
	if calls != 0 {
		t.Errorf("%d calls at construction", calls)
	}
}
