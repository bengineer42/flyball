package proxyauth

import (
	"bytes"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	jose "github.com/go-jose/go-jose/v4"
)

type lockedBuf struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (l *lockedBuf) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

// lines is the logged lines that contain msg.
func (l *lockedBuf) lines(msg string) []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	var out []string
	for _, line := range strings.Split(l.b.String(), "\n") {
		if strings.Contains(line, msg) {
			out = append(out, line)
		}
	}
	return out
}

const (
	jwksFailing   = "keys cannot be fetched"
	jwksRecovered = "keys are fetched again"
)

// A JWKS that stops being fetchable is logged once when fetching starts
// to fail, naming the kind of failure, and once when it works again --
// not on every failed attempt, and never with the token.
func TestJWKSFailureIsLoggedOnceAndRecovery(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("k1", rsaKey, jose.RS256)
	p.setFail(true)
	clk := &clock{t: time.Now()}
	var logs lockedBuf
	rg := cloudflareRig(t, p, Options{Now: clk.now, Logger: slog.New(slog.NewTextHandler(&logs, nil))})
	tok := func() map[string][]string {
		return h("Cf-Access-Jwt-Assertion", sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", clk.now())))
	}
	for range 4 { // four fetches, each failing
		rg.mustStatus(tok(), 503)
		clk.add(RetryAfterFailure + time.Second)
	}
	if n := p.jwksHits(); n != 4 {
		t.Fatalf("%d JWKS fetches, want 4", n)
	}
	failing := logs.lines(jwksFailing)
	if len(failing) != 1 {
		t.Fatalf("%d failure lines after 4 failed fetches, want 1:\n%s", len(failing), strings.Join(failing, "\n"))
	}
	if !strings.Contains(failing[0], "class=") || !strings.Contains(failing[0], "status") {
		t.Errorf("failure line %q does not name the kind of failure (an HTTP status)", failing[0])
	}
	if strings.Contains(failing[0], "eyJ") {
		t.Errorf("failure line %q carries a token", failing[0])
	}

	p.setFail(false)
	rg.mustSub(tok(), "proxy:"+cfIssuer+"#u-1")
	clk.add(MaxKeyAge + time.Minute) // a routine refetch, fine
	rg.mustSub(tok(), "proxy:"+cfIssuer+"#u-1")
	if got := logs.lines(jwksRecovered); len(got) != 1 {
		t.Fatalf("%d recovery lines, want 1:\n%s", len(got), strings.Join(got, "\n"))
	}
	if got := logs.lines(jwksFailing); len(got) != 1 {
		t.Fatalf("%d failure lines, want still 1", len(got))
	}
	t.Logf("logged:\n%s\n%s", failing[0], logs.lines(jwksRecovered)[0])
}

// An IdP that cannot be reached at all from the start (the first signed
// request, at commissioning) is logged too, as a connection failure.
func TestJWKSUnreachableFromTheStartIsLogged(t *testing.T) {
	keys(t)
	p := newIdP(t)
	p.add("k1", rsaKey, jose.RS256)
	p.srv.Close()
	var logs lockedBuf
	rg := cloudflareRig(t, p, Options{Logger: slog.New(slog.NewTextHandler(&logs, nil))})
	rg.mustStatus(h("Cf-Access-Jwt-Assertion", sign(t, rsaKey, jose.RS256, "k1", claims(cfIssuer, "aud-tag-1", "u-1", time.Now()))), 503)
	got := logs.lines(jwksFailing)
	if len(got) != 1 || !strings.Contains(got[0], "class=connect") {
		t.Fatalf("failure lines = %q, want one naming class=connect", got)
	}
}
