package proxyauth

import (
	"context"
	"crypto/ecdsa"
	"crypto/ed25519"
	"crypto/rsa"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
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
		keys: &keySet{url: s.jwks, discover: s.discover, client: o.HTTPClient, now: o.Now, log: o.Logger}}
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
	log      *slog.Logger // nil: nothing logged

	mu        sync.Mutex
	url       string
	keys      []jose.JSONWebKey
	fetched   time.Time // the last successful fetch
	refetched time.Time // the last refetch for an unknown kid
	failedAt  time.Time
	failErr   error
	downSince time.Time // the first failed fetch since the last good one
	inflight  *flight   // the fetch under way, if any
}

// flight is one fetch; the callers that need it wait on done.
type flight struct {
	done chan struct{}
	err  error
}

// lookup is the keys that may have signed a token with this kid (every
// key when there is none). It fetches when there are no keys or they are
// older than MaxKeyAge, and refetches for an unknown kid at most once per
// RefetchEvery (or joins the refetch under way). An error is a failed
// fetch: 503, never anonymous. A fetch runs without ks.mu held, so a
// token whose key is cached never waits on the IdP.
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
	if len(m) == 0 && kid != "" && !fresh &&
		(ks.inflight != nil || ks.refetched.IsZero() || now.Sub(ks.refetched) >= RefetchEvery) {
		if ks.inflight == nil {
			ks.refetched = now
		}
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

// fetch fetches the keys, or waits for the fetch under way. It is called
// with ks.mu held and returns with it held, but releases it while the
// network call runs (sec F4: one slow IdP no longer stalls every request).
func (ks *keySet) fetch(now time.Time) error {
	if f := ks.inflight; f != nil {
		ks.mu.Unlock()
		<-f.done
		ks.mu.Lock()
		return f.err
	}
	if !ks.failedAt.IsZero() && now.Sub(ks.failedAt) < RetryAfterFailure && now.Sub(ks.failedAt) >= 0 {
		return ks.failErr
	}
	f := &flight{done: make(chan struct{})}
	ks.inflight = f
	u := ks.url
	ks.mu.Unlock()
	keys, u, err := ks.fetchNow(u)
	ks.mu.Lock()
	ks.inflight = nil
	if u != "" {
		ks.url = u
	}
	if err != nil {
		if ks.downSince.IsZero() {
			ks.downSince = now
			ks.logf(slog.LevelWarn, "front: the identity provider's keys cannot be fetched; every request carrying its signed assertion answers 503 until they can",
				"class", fetchErrorClass(err), "host", ks.host(u))
		}
		ks.failedAt, ks.failErr = now, err
	} else {
		if !ks.downSince.IsZero() {
			ks.logf(slog.LevelInfo, "front: the identity provider's keys are fetched again",
				"host", ks.host(u), "after", now.Sub(ks.downSince).Round(time.Second).String())
			ks.downSince = time.Time{}
		}
		ks.keys = keys
		ks.failedAt, ks.failErr = time.Time{}, nil
		ks.fetched = now
	}
	f.err = err
	close(f.done)
	return err
}

func (ks *keySet) logf(level slog.Level, msg string, args ...any) {
	if ks.log != nil {
		ks.log.Log(context.Background(), level, msg, args...)
	}
}

// host is u's host (or, before discovery found u, the issuer's): the
// only part of the URL logged, so nothing in a path or query is.
func (ks *keySet) host(u string) string {
	if u == "" {
		u = ks.discover
	}
	if p, err := url.Parse(u); err == nil {
		return p.Host
	}
	return ""
}

// statusError is a fetch the IdP answered with a status other than 200.
type statusError struct{ url, status string }

func (e *statusError) Error() string { return fmt.Sprintf("GET %s: %s", e.url, e.status) }

// fetchErrorClass is the kind of a failed fetch, for the log: the error's
// own text may carry a URL's path and query.
func fetchErrorClass(err error) string {
	var (
		status *statusError
		dns    *net.DNSError
		netErr net.Error
		cert   *tls.CertificateVerificationError
		alert  tls.AlertError
		op     *net.OpError
	)
	switch {
	case errors.As(err, &status):
		return "status " + status.status
	case errors.As(err, &dns):
		return "dns"
	case errors.As(err, &netErr) && netErr.Timeout():
		return "timeout"
	case errors.As(err, &cert), errors.As(err, &alert):
		return "tls"
	case errors.As(err, &op):
		return "connect"
	default:
		return "response" // an answer that is not a usable discovery document or JWKS
	}
}

// fetchNow fetches the JWKS at u (discovering u first when it is ""),
// touching nothing in ks but its immutable fields: it runs without ks.mu.
// The URL it returns is the one it used, once discovered even on an error.
func (ks *keySet) fetchNow(u string) ([]jose.JSONWebKey, string, error) {
	if u == "" {
		var doc struct {
			Issuer  string `json:"issuer"`
			JWKSURI string `json:"jwks_uri"`
		}
		if err := ks.getJSON(strings.TrimSuffix(ks.discover, "/")+"/.well-known/openid-configuration", &doc); err != nil {
			return nil, "", fmt.Errorf("OIDC discovery: %w", err)
		}
		if doc.Issuer != ks.discover {
			return nil, "", fmt.Errorf("OIDC discovery: issuer %q is not %q", doc.Issuer, ks.discover)
		}
		if err := checkFetchURL(doc.JWKSURI); err != nil {
			return nil, "", fmt.Errorf("OIDC discovery: jwks_uri: %v", err)
		}
		u = doc.JWKSURI
	}
	var set struct {
		Keys []json.RawMessage `json:"keys"`
	}
	if err := ks.getJSON(u, &set); err != nil {
		return nil, u, fmt.Errorf("JWKS: %w", err)
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
		return nil, u, errors.New("JWKS: no usable public signing keys at " + u)
	}
	return keys, u, nil
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
		return &statusError{url: u, status: resp.Status}
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
