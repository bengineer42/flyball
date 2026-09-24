package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"flyballd/internal/front"
	"flyballd/internal/front/store"
	"flyballd/internal/frontwire"
)

// auditRecords is every record in the audit beside tokensPath; a line
// that is not one JSON object fails the test.
func auditRecords(t *testing.T, tokensPath string) []map[string]any {
	t.Helper()
	f, err := os.Open(filepath.Join(filepath.Dir(tokensPath), frontwire.AuditFile))
	if err != nil {
		t.Fatalf("the audit: %v", err)
	}
	defer f.Close()
	var out []map[string]any
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var rec map[string]any
		if err := json.Unmarshal(sc.Bytes(), &rec); err != nil {
			t.Fatalf("audit line %q is not one JSON record: %v", sc.Text(), err)
		}
		out = append(out, rec)
	}
	return out
}

func auditRig(t *testing.T) (rig, tokensPath string) {
	t.Helper()
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	rig = filepath.Join(t.TempDir(), "blender.yaml")
	os.WriteFile(rig, []byte("devices: {}\n"), 0o644)
	tokensPath, err := tokensPathFor(rig, false)
	if err != nil {
		t.Fatal(err)
	}
	return rig, tokensPath
}

func onlyToken(t *testing.T, tokensPath string) (store.Token, bool) {
	t.Helper()
	tokens, err := store.OpenTokens(tokensPath, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer tokens.Close()
	list, err := tokens.List()
	if err != nil {
		t.Fatal(err)
	}
	if len(list) == 0 {
		return store.Token{}, false
	}
	return list[0], true
}

// TestTokenCreateAndRevokeAreAudited: `flyball token create` and `revoke`
// append to the front's own audit (audit.jsonl beside tokens.json) the
// records the token routes write, by local:cli -- a revoke of an unknown
// id with its outcome, as the front records it.
func TestTokenCreateAndRevokeAreAudited(t *testing.T) {
	rig, tokensPath := auditRig(t)
	out := captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "ci", "--config", rig, "--scope", "operate:blender", "--kind", "service"}); err != nil {
			t.Fatal(err)
		}
	})
	secret := strings.TrimSpace(out)
	tok, ok := onlyToken(t, tokensPath)
	if !ok {
		t.Fatal("no token was created")
	}
	captureStdout(t, func() {
		if err := runTokenRevoke([]string{tok.ID, "--config", rig}); err != nil {
			t.Fatal(err)
		}
		if err := runTokenRevoke([]string{"tk_nosuch", "--config", rig}); err == nil {
			t.Fatal("revoking an unknown id succeeded")
		}
	})

	recs := auditRecords(t, tokensPath)
	raw, _ := os.ReadFile(filepath.Join(filepath.Dir(tokensPath), frontwire.AuditFile))
	if strings.Contains(string(raw), secret) {
		t.Fatal("the audit holds the token's secret")
	}
	want := []map[string]string{
		{"event": "token.create", "by": "local:cli", "id": tok.ID, "name": "ci", "kind": "service"},
		{"event": "token.revoke", "by": "local:cli", "id": tok.ID, "outcome": "revoked"},
		{"event": "token.revoke", "by": "local:cli", "id": "tk_nosuch", "outcome": "not found"},
	}
	if len(recs) != len(want) {
		t.Fatalf("audit = %v, want %d records", recs, len(want))
	}
	for i, w := range want {
		for k, v := range w {
			if got := fmt.Sprint(recs[i][k]); got != v {
				t.Errorf("record %d %s = %q, want %q (%v)", i, k, got, v, recs[i])
			}
		}
		for _, k := range []string{"time", "seq", "boot"} {
			if _, ok := recs[i][k]; !ok {
				t.Errorf("record %d has no %s: %v", i, k, recs[i])
			}
		}
	}
	if scopes := fmt.Sprint(recs[0]["scopes"]); !strings.Contains(scopes, "operate:blender") {
		t.Errorf("token.create scopes = %s, want operate:blender among them", scopes)
	}
}

// unopenableAudit puts a directory where the audit file goes.
func unopenableAudit(t *testing.T, tokensPath string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(filepath.Dir(tokensPath), frontwire.AuditFile), 0o700); err != nil {
		t.Fatal(err)
	}
}

// TestTokenCreateRefusedWithoutAnAudit: a token whose record cannot be
// written is not created (the front's rule for a token change): an
// error naming the audit, no secret printed, nothing in the tokens file.
func TestTokenCreateRefusedWithoutAnAudit(t *testing.T) {
	rig, tokensPath := auditRig(t)
	unopenableAudit(t, tokensPath)
	var err error
	out := captureStdout(t, func() {
		err = runTokenCreate([]string{"--name", "ci", "--config", rig})
	})
	if err == nil || !strings.Contains(err.Error(), "audit") {
		t.Fatalf("err = %v, want a refusal naming the audit", err)
	}
	if strings.TrimSpace(out) != "" {
		t.Errorf("stdout = %q, want no secret", out)
	}
	if tok, ok := onlyToken(t, tokensPath); ok {
		t.Errorf("token %s was created with no audit record", tok.ID)
	}
}

// TestTokenRevokeWithoutAnAuditStillRevokes: a revoke is the safe
// direction, so it happens, and the error says it could not be recorded
// (the front answers 503 the same way).
func TestTokenRevokeWithoutAnAuditStillRevokes(t *testing.T) {
	rig, tokensPath := auditRig(t)
	captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "ci", "--config", rig}); err != nil {
			t.Fatal(err)
		}
	})
	tok, _ := onlyToken(t, tokensPath)
	os.Remove(filepath.Join(filepath.Dir(tokensPath), frontwire.AuditFile))
	unopenableAudit(t, tokensPath)
	var err error
	captureStdout(t, func() { err = runTokenRevoke([]string{tok.ID, "--config", rig}) })
	if err == nil || !strings.Contains(err.Error(), "audit") || !strings.Contains(err.Error(), "revoked") {
		t.Fatalf("err = %v, want one saying the token is revoked but the audit cannot be written", err)
	}
	if _, ok := onlyToken(t, tokensPath); ok {
		t.Error("the token was not revoked")
	}
}

// TestTokenAuditBesideALiveFront: the CLI and a running front append to
// one audit file at once (each its own O_APPEND descriptor, as two
// processes would have): every line stays one whole record.
func TestTokenAuditBesideALiveFront(t *testing.T) {
	rig, tokensPath := auditRig(t)
	if err := os.MkdirAll(filepath.Dir(tokensPath), 0o700); err != nil {
		t.Fatal(err)
	}
	live, err := front.OpenAudit(filepath.Join(filepath.Dir(tokensPath), frontwire.AuditFile))
	if err != nil {
		t.Fatal(err)
	}
	defer live.Close()
	const n = 200
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := range n {
			live.Event("login.fail", slog.String("peer", strings.Repeat("x", 500)), slog.Int("i", i))
		}
	}()
	captureStdout(t, func() {
		for i := range 20 {
			if err := runTokenCreate([]string{"--name", fmt.Sprint("ci", i), "--config", rig}); err != nil {
				t.Error(err)
			}
		}
	})
	wg.Wait()
	recs := auditRecords(t, tokensPath)
	if len(recs) != n+20 {
		t.Fatalf("%d records, want %d", len(recs), n+20)
	}
}
