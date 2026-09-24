// Package proxyauth is the proxy shape's provider: `auth: proxy` plus one
// `proxy: {preset: ...}` line puts flyball behind an external identity
// layer (brain design auth.md § Trusted header).
//
// Two kinds of preset:
//
//   - Signed (pomerium, cloudflare, authentik with issuer:, oauth2-proxy with
//     issuer:, custom with jwt:): a JWT verified against the issuer's JWKS
//     with a pinned algorithm list (never none, never HS*), the exact iss,
//     an aud containing the configured audience, and exp/nbf/iat with 60 s
//     skew. A JWKS that cannot be fetched is an error (503), never anonymous.
//   - Unsigned (tailscale, authelia, authentik and oauth2-proxy without
//     issuer:, custom with user_header:): plain headers, believed only from
//     a vouched-for peer -- the front's own unix socket (`from: unix`, the
//     default; the peer's uid is recorded from SO_PEERCRED), or a peer
//     allow-list, where a loopback or local-interface address also needs
//     secret_file: sent as X-Flyball-Proxy-Secret. Anything else refuses
//     the shape, and the front falls back to local on loopback (D-028).
//     From a peer that is not vouched for, the headers are ignored.
//
// An identity is (issuer, subject): the front makes the principal's sub
// proxy:<issuer>#<subject>. The subject is the IdP's stable id (JWT sub,
// authentik uid, the Authelia or oauth2-proxy username, the Tailscale
// login); for the unsigned presets the issuer is the preset's name. Email
// is never read as a subject and never grants (F17). Groups are the ids the
// proxy sends. The front applies proxy.grants (grants.Match): a user who
// matches none gets read.
//
// The front forwards an allow-list of headers (§WP0-11), so no identity
// header, and no secret, reaches a runner.
//
// JOSE is github.com/go-jose/go-jose/v4 (v4.1.5), Apache License 2.0.
package proxyauth

import (
	"fmt"
	"log/slog"
	"net/http"
	"net/netip"
	"net/textproto"
	"net/url"
	"slices"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"flyballd/internal/front"
)

// ProviderName is what Name returns: every accepted identity's sub is
// proxy:<issuer>#<subject>.
const ProviderName = "proxy"

// SecretHeader carries secret_file's value from the proxy.
const SecretHeader = "X-Flyball-Proxy-Secret"

// Options are what a preset needs beyond `proxy:` and the plan.
type Options struct {
	// Logger is for operational messages; nil: slog.Default().
	Logger *slog.Logger
	// Audit, if set, receives `proxy.peer` (the SO_PEERCRED uid behind
	// each new subject on a unix socket). nil: the Logger gets it.
	Audit *front.Audit
	// HTTPClient fetches JWKS and OIDC discovery; nil: a client with a 10 s
	// timeout that follows no redirect.
	HTTPClient *http.Client
	// Now is the clock for token validity and JWKS refetch; nil: time.Now.
	Now func() time.Time
	// LocalAddrs lists this host's own addresses (a peer allow-list naming
	// one needs secret_file:); nil: the interface addresses.
	LocalAddrs func() ([]netip.Addr, error)
}

// Factory is the front.ProxyFactory to hand front.ResolveWith:
//
//	plan, proxy := front.ResolveWith(cfg, insecureOpen, proxyauth.Factory(proxyauth.Options{Logger: log}))
func Factory(o Options) front.ProxyFactory {
	return func(c *front.ProxyConfig, p front.Plan) (front.Client, error) { return New(c, p, o) }
}

// New builds the provider for c. An error means the shape cannot be
// vouched for: the front falls back to local on loopback, with the error
// as the banner's reason. It makes no network call.
func New(c *front.ProxyConfig, p front.Plan, o Options) (front.Client, error) {
	if c == nil {
		return nil, fmt.Errorf("no proxy: block")
	}
	if o.Logger == nil {
		o.Logger = slog.Default()
	}
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.HTTPClient == nil {
		o.HTTPClient = &http.Client{Timeout: 10 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	if o.LocalAddrs == nil {
		o.LocalAddrs = interfaceAddrs
	}
	signedKeys := c.Issuer != "" || c.Audience != "" || c.Team != "" || c.JWT != nil
	unsignedKeys := c.UserHeader != "" || c.GroupsHeader != "" || c.Separator != ""

	switch c.Preset {
	case "tailscale":
		if signedKeys || unsignedKeys {
			return nil, fmt.Errorf("preset tailscale takes from: and secret_file: only (no issuer, audience, team, jwt or *_header keys)")
		}
		return newUnsigned(c, p, o, headerSet{issuer: "tailscale", user: "Tailscale-User-Login", name: "Tailscale-User-Name",
			extra: []string{"Tailscale-User-Profile-Pic"}, qDecode: true})
	case "authelia":
		if signedKeys || unsignedKeys {
			return nil, fmt.Errorf("preset authelia takes from: and secret_file: only (no issuer, audience, team, jwt or *_header keys)")
		}
		return newUnsigned(c, p, o, headerSet{issuer: "authelia", user: "Remote-User", groups: "Remote-Groups", sep: ",",
			name: "Remote-Name", extra: []string{"Remote-Email"}})
	case "oauth2-proxy":
		if c.Team != "" || c.JWT != nil || unsignedKeys {
			return nil, fmt.Errorf("preset oauth2-proxy takes issuer: and audience: (signed) or from: (unsigned)")
		}
		if c.Issuer != "" || c.Audience != "" {
			if len(c.From) > 0 || c.SecretFile != "" {
				return nil, fmt.Errorf("oauth2-proxy: set issuer: and audience: (signed) or from: (unsigned), not both")
			}
			if c.Issuer == "" || c.Audience == "" {
				return nil, fmt.Errorf("oauth2-proxy signed needs issuer: and audience: (the client id)")
			}
			if err := checkFetchURL(c.Issuer); err != nil {
				return nil, fmt.Errorf("oauth2-proxy issuer: %v", err)
			}
			return newJWT(o, jwtSpec{header: "Authorization", bearer: true, issuer: c.Issuer, audience: c.Audience,
				algs: []string{"RS256", "ES256"}, discover: c.Issuer})
		}
		return newUnsigned(c, p, o, headerSet{issuer: "oauth2-proxy", user: "X-Forwarded-User", groups: "X-Forwarded-Groups", sep: ",",
			extra: []string{"X-Forwarded-Email", "X-Forwarded-Preferred-Username", "X-Forwarded-Access-Token"}, email: "X-Forwarded-Email"})
	case "authentik":
		if c.Team != "" || c.JWT != nil || unsignedKeys {
			return nil, fmt.Errorf("preset authentik takes issuer: (signed) or from: (unsigned)")
		}
		if c.Issuer != "" || c.Audience != "" {
			if len(c.From) > 0 || c.SecretFile != "" {
				return nil, fmt.Errorf("authentik: set issuer: (signed) or from: (unsigned), not both")
			}
			if c.Issuer == "" {
				return nil, fmt.Errorf("authentik signed needs issuer: (https://<authentik>/application/o/<slug>/)")
			}
			if err := checkFetchURL(c.Issuer); err != nil {
				return nil, fmt.Errorf("authentik issuer: %v", err)
			}
			return newJWT(o, jwtSpec{header: "X-Authentik-Jwt", issuer: c.Issuer, audience: c.Audience,
				algs: []string{"RS256", "ES256"}, jwks: strings.TrimSuffix(c.Issuer, "/") + "/jwks/"})
		}
		return newUnsigned(c, p, o, headerSet{issuer: "authentik", user: "X-Authentik-Uid", groups: "X-Authentik-Groups", sep: "|",
			name: "X-Authentik-Name", extra: []string{"X-Authentik-Username", "X-Authentik-Email", "X-Authentik-Jwt", "X-Authentik-Meta-Jwks",
				"X-Authentik-Meta-Outpost", "X-Authentik-Meta-Provider", "X-Authentik-Meta-App", "X-Authentik-Meta-Version"}})
	case "pomerium":
		if len(c.From) > 0 || c.SecretFile != "" || c.Team != "" || c.JWT != nil || unsignedKeys {
			return nil, fmt.Errorf("preset pomerium is signed: it takes issuer: and audience: overrides only (not from:, team:, jwt: or *_header)")
		}
		if p.URL == nil {
			return nil, fmt.Errorf("preset pomerium needs url: (the route's https:// URL; its JWKS is there)")
		}
		if p.URL.Scheme != "https" {
			return nil, fmt.Errorf("preset pomerium needs an https url:, not %s", p.URL)
		}
		iss, aud := c.Issuer, c.Audience
		if iss == "" {
			iss = p.URL.Host
		}
		if aud == "" {
			aud = p.URL.Host
		}
		return newJWT(o, jwtSpec{header: "X-Pomerium-Jwt-Assertion", issuer: iss, audience: aud,
			algs: []string{"ES256"}, jwks: "https://" + p.URL.Host + "/.well-known/pomerium/jwks.json"})
	case "cloudflare":
		if len(c.From) > 0 || c.SecretFile != "" || c.Issuer != "" || c.JWT != nil || unsignedKeys {
			return nil, fmt.Errorf("preset cloudflare is signed: it takes team: and audience: only (not from:, issuer:, jwt: or *_header)")
		}
		if c.Team == "" || c.Audience == "" {
			return nil, fmt.Errorf("preset cloudflare needs team: (<team>.cloudflareaccess.com) and audience: (the application's AUD tag)")
		}
		if !dnsLabel(c.Team) {
			return nil, fmt.Errorf("cloudflare team: %q is not a DNS label", c.Team)
		}
		base := "https://" + strings.ToLower(c.Team) + ".cloudflareaccess.com"
		return newJWT(o, jwtSpec{header: "Cf-Access-Jwt-Assertion", issuer: base, audience: c.Audience,
			algs: []string{"RS256"}, jwks: base + "/cdn-cgi/access/certs"})
	case "custom":
		if c.Issuer != "" || c.Audience != "" || c.Team != "" {
			return nil, fmt.Errorf("preset custom: put issuer and audience inside jwt:")
		}
		switch {
		case c.JWT != nil && unsignedKeys:
			return nil, fmt.Errorf("custom: set jwt: (signed) or user_header: (unsigned), not both")
		case c.JWT != nil:
			if len(c.From) > 0 || c.SecretFile != "" {
				return nil, fmt.Errorf("custom jwt: is signed; from: and secret_file: are for user_header:")
			}
			return newCustomJWT(c.JWT, o)
		case c.UserHeader == "":
			return nil, fmt.Errorf("custom needs user_header: (unsigned) or jwt: (signed)")
		}
		hs := headerSet{issuer: "custom", groups: c.GroupsHeader, sep: c.Separator}
		if hs.sep == "" {
			hs.sep = ","
		}
		var err error
		if hs.user, err = customHeader("user_header", c.UserHeader); err != nil {
			return nil, err
		}
		if c.GroupsHeader != "" {
			if hs.groups, err = customHeader("groups_header", c.GroupsHeader); err != nil {
				return nil, err
			}
		}
		return newUnsigned(c, p, o, hs)
	case "":
		return nil, fmt.Errorf("proxy: needs a preset: (tailscale, authelia, oauth2-proxy, authentik, pomerium, cloudflare, custom)")
	default:
		return nil, fmt.Errorf("preset %q is not one of tailscale, authelia, oauth2-proxy, authentik, pomerium, cloudflare, custom", c.Preset)
	}
}

func newCustomJWT(j *front.CustomJWT, o Options) (front.Client, error) {
	switch {
	case j.Header == "" || j.JWKSURL == "" || j.Issuer == "" || j.Audience == "":
		return nil, fmt.Errorf("custom jwt: needs header, jwks_url, issuer, audience and algorithms")
	case len(j.Algorithms) == 0:
		return nil, fmt.Errorf("custom jwt: needs algorithms: (e.g. [RS256]); none and HS* are refused")
	}
	for _, a := range j.Algorithms {
		if !slices.Contains(asymmetric, a) {
			return nil, fmt.Errorf("custom jwt.algorithms: %q is refused; use asymmetric ones (%s), never none or HS*",
				a, strings.Join(asymmetric, ", "))
		}
	}
	h, err := customHeader("jwt.header", j.Header)
	if err != nil && !strings.EqualFold(j.Header, "Authorization") {
		return nil, err
	}
	if err := checkFetchURL(j.JWKSURL); err != nil {
		return nil, fmt.Errorf("custom jwt.jwks_url: %v", err)
	}
	bearer := strings.EqualFold(j.Header, "Authorization")
	if bearer {
		h = "Authorization"
	}
	return newJWT(o, jwtSpec{header: h, bearer: bearer, issuer: j.Issuer, audience: j.Audience, algs: j.Algorithms, jwks: j.JWKSURL})
}

// asymmetric is every algorithm a jwt: block may name.
var asymmetric = []string{"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}

// reserved are headers a custom preset may not read an identity from: the
// front's own credentials and routing.
var reserved = []string{"Authorization", "Cookie", "Host", "Origin", "Forwarded", "X-Forwarded-For", "X-Forwarded-Host",
	"X-Forwarded-Proto", "X-Real-Ip", "X-Request-Id", SecretHeader}

// customHeader checks a configured header name and canonicalises it.
func customHeader(key, name string) (string, error) {
	if !validHeaderName(name) {
		return "", fmt.Errorf("custom %s: %q is not a header name", key, name)
	}
	c := textproto.CanonicalMIMEHeaderKey(name)
	for _, r := range reserved {
		if strings.EqualFold(c, r) {
			return "", fmt.Errorf("custom %s: %s is the front's own header", key, c)
		}
	}
	if strings.HasPrefix(strings.ToLower(c), "x-flyball-") {
		return "", fmt.Errorf("custom %s: %s is the front's own header", key, c)
	}
	return c, nil
}

// validHeaderName: letters, digits and '-' (no '_': underscore spellings
// are the spoofing channel, adv-refs).
func validHeaderName(s string) bool {
	if s == "" || len(s) > 64 {
		return false
	}
	for _, r := range s {
		if !(r == '-' || r < utf8.RuneSelf && (unicode.IsLetter(r) || unicode.IsDigit(r))) {
			return false
		}
	}
	return true
}

// dnsLabel: a Cloudflare team name is one DNS label.
func dnsLabel(s string) bool {
	if s == "" || len(s) > 63 || s[0] == '-' || s[len(s)-1] == '-' {
		return false
	}
	for _, r := range s {
		if !(r == '-' || r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9') {
			return false
		}
	}
	return true
}

// checkFetchURL: something the front fetches keys from must be https,
// or http on a loopback host (an IdP on this machine).
func checkFetchURL(s string) error {
	u, err := url.Parse(s)
	if err != nil || u.Host == "" || u.User != nil {
		return fmt.Errorf("%q is not an absolute URL", s)
	}
	switch u.Scheme {
	case "https":
		return nil
	case "http":
		h := u.Hostname()
		if h == "localhost" {
			return nil
		}
		if a, err := netip.ParseAddr(h); err == nil && a.IsLoopback() {
			return nil
		}
	}
	return fmt.Errorf("%q must be https (http only for a loopback host)", s)
}

// cleanID is an identity string as the principal can carry it: at most
// 512 bytes, valid UTF-8, no control characters or U+2028/9.
func cleanID(s string) bool {
	if len(s) > 512 || !utf8.ValidString(s) {
		return false
	}
	for _, r := range s {
		if unicode.IsControl(r) || r == ' ' || r == ' ' {
			return false
		}
	}
	return true
}

// splitGroups is every value of a groups header, each split on sep,
// trimmed, empties and bad ids dropped, at most 256.
func splitGroups(values []string, sep string) []string {
	var out []string
	for _, v := range values {
		for _, g := range strings.Split(v, sep) {
			g = strings.TrimSpace(g)
			if g != "" && cleanID(g) && !slices.Contains(out, g) && len(out) < 256 {
				out = append(out, g)
			}
		}
	}
	return out
}
