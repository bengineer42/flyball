package proxyauth

import (
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/rsa"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"flyballd/internal/front"

	jose "github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

// The JWKS cache's timings (auth.md § Trusted header).
const (
	// Skew is the leeway on exp, nbf and iat.
	Skew = 60 * time.Second
	// RefetchEvery bounds refetches for an unknown kid: at most one per.
	RefetchEvery = 60 * time.Second
	// MaxKeyAge: keys older than this are refetched before use, so a key
	// the IdP withdrew stops verifying.
	MaxKeyAge = time.Hour
	// RetryAfterFailure: a failed fetch is not retried sooner (every
	// request meanwhile is 503).
	RetryAfterFailure = 5 * time.Second

	maxJWKSBytes  = 1 << 20
	maxTokenBytes = 16 << 10
)

// jwtSpec is a signed preset.
type jwtSpec struct {
	header   string
	bearer   bool // the header is Authorization: Bearer <jwt>
	issuer   string
	audience string // "" = not checked (authentik without audience:)
	algs     []string
	jwks     string // the JWKS URL, or "" to discover it
	discover string // the issuer to run OIDC discovery on
}

type jwtClient struct {
	spec jwtSpec
	algs []jose.SignatureAlgorithm
	now  func() time.Time
	keys *keySet
}

func newJWT(o Options, s jwtSpec) (front.Client, error) {
	c := &jwtClient{spec: s, now: o.Now,
		keys: &keySet{url: s.jwks, discover: s.discover, client: o.HTTPClient, now: o.Now}}
	for _, a := range s.algs {
		c.algs = append(c.algs, jose.SignatureAlgorithm(a))
	}
	return c, nil
}

func (c *jwtClient) Name() string { return ProviderName }

// claimsExtra are the non-registered claims read: groups and a display
// name. email is never read.
type claimsExtra struct {
	Groups            json.RawMessage `json:"groups"`
	Name              string          `json:"name"`
	PreferredUsername string          `json:"preferred_username"`
}

func (c *jwtClient) Authenticate(r *http.Request) (front.Identity, front.Outcome, error) {
	vals := r.Header.Values(c.spec.header)
	if len(vals) == 0 {
		return front.Identity{}, front.NotMine, nil
	}
	if len(vals) > 1 {
		return front.Identity{}, front.Reject, nil
	}
	raw := strings.TrimSpace(vals[0])
	if c.spec.bearer {
		scheme, cred, _ := strings.Cut(raw, " ")
		if !strings.EqualFold(scheme, "Bearer") {
			return front.Identity{}, front.NotMine, nil
		}
		raw = strings.TrimSpace(cred)
	}
	if raw == "" || len(raw) > maxTokenBytes {
		return front.Identity{}, front.Reject, nil
	}
	tok, err := jwt.ParseSigned(raw, c.algs) // an alg off the list, none and HS* fail here
	if err != nil || len(tok.Headers) != 1 {
		return front.Identity{}, front.Reject, nil
	}
	hdr := tok.Headers[0]
	keys, err := c.keys.lookup(hdr.KeyID)
	if err != nil {
		return front.Identity{}, front.Reject, err // 503: the JWKS could not be fetched
	}
	var cl jwt.Claims
	var ex claimsExtra
	verified := false
	for _, k := range keys {
		if k.Algorithm != "" && k.Algorithm != hdr.Algorithm {
			continue
		}
		if err := tok.Claims(k.Key, &cl, &ex); err == nil {
			verified = true
			break
		}
	}
	if !verified || cl.Expiry == nil {
		return front.Identity{}, front.Reject, nil
	}
	want := jwt.Expected{Issuer: c.spec.issuer, Time: c.now()}
	if c.spec.audience != "" {
		want.AnyAudience = jwt.Audience{c.spec.audience}
	}
	if cl.ValidateWithLeeway(want, Skew) != nil || cl.Issuer != c.spec.issuer {
		return front.Identity{}, front.Reject, nil
	}
	if cl.Subject == "" || !cleanID(cl.Subject) {
		return front.Identity{}, front.Reject, nil
	}
	id := front.Identity{Issuer: cl.Issuer, Subject: cl.Subject, Groups: groupsClaim(ex.Groups)}
	for _, n := range []string{ex.Name, ex.PreferredUsername} {
		if n != "" && cleanID(n) {
			id.Name = n
			break
		}
	}
	return id, front.Accept, nil
}

// groupsClaim reads `groups` as a list of strings or one string.
func groupsClaim(raw json.RawMessage) []string {
	if len(raw) == 0 {
		return nil
	}
	var list []string
	if json.Unmarshal(raw, &list) == nil {
		return splitGroups(list, "\x00")
	}
	var one string
	if json.Unmarshal(raw, &one) == nil {
		return splitGroups([]string{one}, "\x00")
	}
	return nil
}

// keySet is one issuer's JWKS, cached.
type keySet struct {
	client   *http.Client
	now      func() time.Time
	discover string

	mu        sync.Mutex
	url       string
	keys      []jose.JSONWebKey
	fetched   time.Time // the last successful fetch
	refetched time.Time // the last refetch for an unknown kid
	failedAt  time.Time
	failErr   error
}

// lookup is the keys that may have signed a token with this kid (every
// key when there is none). It fetches when there are no keys or they are
// older than MaxKeyAge, and refetches for an unknown kid at most once per
// RefetchEvery. An error is a failed fetch: 503, never anonymous.
func (ks *keySet) lookup(kid string) ([]jose.JSONWebKey, error) {
	ks.mu.Lock()
	defer ks.mu.Unlock()
	now := ks.now()
	fresh := false
	if ks.keys == nil || now.Sub(ks.fetched) > MaxKeyAge {
		if err := ks.fetch(now); err != nil {
			return nil, err
		}
		fresh = true
	}
	m := ks.match(kid)
	if len(m) == 0 && kid != "" && !fresh && (ks.refetched.IsZero() || now.Sub(ks.refetched) >= RefetchEvery) {
		ks.refetched = now
		if err := ks.fetch(now); err != nil {
			return nil, err
		}
		m = ks.match(kid)
	}
	return m, nil
}

func (ks *keySet) match(kid string) []jose.JSONWebKey {
	if kid == "" {
		return ks.keys
	}
	var out []jose.JSONWebKey
	for _, k := range ks.keys {
		if k.KeyID == kid {
			out = append(out, k)
		}
	}
	return out
}

func (ks *keySet) fetch(now time.Time) error {
	if !ks.failedAt.IsZero() && now.Sub(ks.failedAt) < RetryAfterFailure && now.Sub(ks.failedAt) >= 0 {
		return ks.failErr
	}
	err := ks.fetchNow()
	if err != nil {
		ks.failedAt, ks.failErr = now, err
		return err
	}
	ks.failedAt, ks.failErr = time.Time{}, nil
	ks.fetched = now
	return nil
}

func (ks *keySet) fetchNow() error {
	if ks.url == "" {
		var doc struct {
			Issuer  string `json:"issuer"`
			JWKSURI string `json:"jwks_uri"`
		}
		if err := ks.getJSON(strings.TrimSuffix(ks.discover, "/")+"/.well-known/openid-configuration", &doc); err != nil {
			return fmt.Errorf("OIDC discovery: %w", err)
		}
		if doc.Issuer != ks.discover {
			return fmt.Errorf("OIDC discovery: issuer %q is not %q", doc.Issuer, ks.discover)
		}
		if err := checkFetchURL(doc.JWKSURI); err != nil {
			return fmt.Errorf("OIDC discovery: jwks_uri: %v", err)
		}
		ks.url = doc.JWKSURI
	}
	var set struct {
		Keys []json.RawMessage `json:"keys"`
	}
	if err := ks.getJSON(ks.url, &set); err != nil {
		return fmt.Errorf("JWKS: %w", err)
	}
	var keys []jose.JSONWebKey
	for _, raw := range set.Keys {
		var k jose.JSONWebKey
		if json.Unmarshal(raw, &k) != nil || !k.Valid() || k.Use == "enc" {
			continue // a key type or use this cannot verify with: skipped, not fatal
		}
		if !k.IsPublic() {
			k = k.Public()
		}
		switch pk := k.Key.(type) {
		case *rsa.PublicKey:
			if pk.N.BitLen() < 2048 {
				continue
			}
		case *ecdsa.PublicKey, ed25519.PublicKey:
		default:
			continue // symmetric or unknown: never a verification key here
		}
		keys = append(keys, k)
	}
	if len(keys) == 0 {
		return errors.New("JWKS: no usable public signing keys at " + ks.url)
	}
	ks.keys = keys
	return nil
}

func (ks *keySet) getJSON(u string, v any) error {
	req, err := http.NewRequest("GET", u, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	resp, err := ks.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: %s", u, resp.Status)
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, maxJWKSBytes+1))
	if err != nil {
		return err
	}
	if len(b) > maxJWKSBytes {
		return fmt.Errorf("GET %s: more than %d bytes", u, maxJWKSBytes)
	}
	return json.Unmarshal(b, v)
}
