package client

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/front"
	"flyballd/internal/front/store"
	"flyballd/internal/principal"

	"golang.org/x/crypto/scrypt"
)

// testPassword is the admin password testScrypt line verifies, mirroring
// daemon/internal/front's own helpers_test.go (n=16: cheap, not a real
// front's parameters, so the tests stay fast).
const testPassword = "correct horse"

func scryptLine(password string, n int) string {
	salt := []byte("0123456789abcdef")
	sum, err := scrypt.Key([]byte(password), salt, n, 1, 1, 64)
	if err != nil {
		panic(err)
	}
	enc := base64.RawURLEncoding.EncodeToString
	return fmt.Sprintf("$scrypt$n=%d,r=1,p=1$%s$%s", n, enc(salt), enc(sum))
}

// echoRunner is a fake fronted runner good enough for these tests: it
// verifies every request's principal and answers the readiness handshake
// and the stop route with a canned report.
type echoRunner struct {
	t   *testing.T
	ep  endpoint.Endpoint
	key principal.Key
	aud string
}

func newEchoRunner(t *testing.T, aud string) *echoRunner {
	t.Helper()
	dir := t.TempDir()
	er := &echoRunner{t: t, aud: aud, ep: endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, "sock")}}
	var key [32]byte
	for i := range key {
		key[i] = byte(i + 1)
	}
	er.key = key
	ln, err := net.Listen("unix", er.ep.Address)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(er.serve)}
	go srv.Serve(ln)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	})
	return er
}

func (er *echoRunner) target() front.Target {
	return front.Target{Endpoint: er.ep, Aud: er.aud, Key: er.key}
}

func (er *echoRunner) serve(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/api/auth/front" {
		if len(r.Header.Values(principal.Header)) == 0 {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"protocol": 1, "aud": er.aud, "pid": os.Getpid(), "flyball": "test"})
		return
	}
	tok := r.Header.Get(principal.Header)
	if tok == "" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	claims, err := principal.Verify(tok, er.key, er.aud, time.Now())
	if err != nil {
		w.Header().Set("X-Flyball-Principal-Error", err.(*principal.Error).Code)
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	if r.URL.Path == "/api/rig/stop" {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"at_ns":  time.Now().UnixNano(),
			"actor":  map[string]string{"sub": claims.Sub, "sid": claims.Sid, "kind": claims.Kind, "via": "http"},
			"reason": "flyball stop", "devices": map[string]any{}, "program_interrupted": false,
			"controllers_manual": []string{}, "interim": true,
		})
		return
	}
	w.WriteHeader(http.StatusNotFound)
}

// newTestFront builds a real front (password shape) over an httptest
// server, its own tokens.json in t.TempDir(), and fronting er.
func newTestFront(t *testing.T, er *echoRunner) (*httptest.Server, string) {
	t.Helper()
	plan := front.Resolve(front.Config{Auth: "password", Password: scryptLine(testPassword, 16)}, false)
	tokensPath := filepath.Join(t.TempDir(), "front", "tokens.json")
	opts := front.Options{
		Plan: plan,
		Route: front.SingleRig(front.Rig{Name: "blender", Target: func(context.Context) (front.Target, error) {
			return er.target(), nil
		}}),
		TokensPath: tokensPath,
		FailDelay:  time.Millisecond,
		Sweep:      20 * time.Millisecond,
	}
	f := front.New(opts)
	srv := httptest.NewServer(f)
	t.Cleanup(func() {
		srv.CloseClientConnections()
		f.Close()
		srv.Close()
		plan.Close()
	})
	return srv, tokensPath
}

func TestLoginSavesATokenThatWorksAgainstAFreshTarget(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, _ := newTestFront(t, er)

	target := Target{BaseURL: srv.URL}
	tok, err := Login(target, testPassword, LoginOptions{})
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if tok.Name == "" || len(tok.Scopes) == 0 {
		t.Fatalf("Login returned an empty token: %+v", tok)
	}
	if tok.Scopes[0] != "read:*" {
		t.Errorf("scopes = %v, want [read:*] (the default per auth.md)", tok.Scopes)
	}
	if tok.Elevated {
		t.Errorf("Elevated = true for a default read-only login")
	}

	// A fresh Target (no WithToken) must pick the saved token back up.
	fresh := Target{BaseURL: srv.URL}
	var out any
	if err := fresh.Do("GET", "/api/auth", nil, &out); err != nil {
		t.Fatalf("GET /api/auth with the saved token: %v", err)
	}
	m := out.(map[string]any)
	if m["scheme"] != "token" {
		t.Errorf("scheme = %v, want token (the saved login token should have been sent)", m["scheme"])
	}
}

func TestLoginWrongPassword(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, _ := newTestFront(t, er)

	if _, err := Login(Target{BaseURL: srv.URL}, "wrong", LoginOptions{}); err == nil {
		t.Fatal("Login with the wrong password did not error")
	}
}

func TestLogoutClearsTheSavedTokenOnly(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, _ := newTestFront(t, er)

	target := Target{BaseURL: srv.URL}
	if _, err := Login(target, testPassword, LoginOptions{}); err != nil {
		t.Fatal(err)
	}
	if err := Logout(target); err != nil {
		t.Fatal(err)
	}
	if h := target.AuthHeaders(); h.Get("Authorization") != "" {
		t.Errorf("AuthHeaders after logout carried a credential: %v", h)
	}
}

// TestOfflineTokenAcceptedWithoutRestart is the merge-critical case
// (A3 as built): a token written straight into the tokens.json file
// while the front is already running (as `flyball token create` does,
// store.OpenTokens against the same path) is accepted at the front's
// very next request -- no restart, because store.Tokens re-reads the
// file on stat change.
func TestOfflineTokenAcceptedWithoutRestart(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, tokensPath := newTestFront(t, er)

	// The front is already up and has already been asked once (so its
	// Tokens handle exists and has a baseline stat), *then* the file is
	// written offline.
	var out any
	if err := (Target{BaseURL: srv.URL}).Do("GET", "/api/auth", nil, &out); err != nil {
		t.Fatal(err)
	}

	tokens, err := store.OpenTokens(tokensPath, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	secret, _, err := tokens.Create(store.NewToken{Name: "offline", Scopes: []string{"read:*"}})
	if err != nil {
		t.Fatal(err)
	}
	tokens.Close()

	target := Target{BaseURL: srv.URL}.WithToken(secret)
	var info any
	if err := target.Do("GET", "/api/auth", nil, &info); err != nil {
		t.Fatalf("the offline token was refused: %v", err)
	}
	m := info.(map[string]any)
	if m["scheme"] != "token" {
		t.Errorf("scheme = %v, want token", m["scheme"])
	}
}
