package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"flyballd/internal/front/store"
)

func TestTokensPathForRigFile(t *testing.T) {
	stateHome := t.TempDir()
	t.Setenv("XDG_STATE_HOME", stateHome)

	rig := filepath.Join(t.TempDir(), "blender.yaml")
	os.WriteFile(rig, []byte("devices: {}\n"), 0o644)

	path, err := tokensPathFor(rig)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(path, filepath.Join(stateHome, "flyball", "front-")) || !strings.HasSuffix(path, "tokens.json") {
		t.Errorf("path = %q, want under %s/flyball/front-<id>/tokens.json", path, stateHome)
	}
}

func TestTokensPathForDaemonConfig(t *testing.T) {
	dir := t.TempDir()
	daemonYAML := filepath.Join(dir, "flyballd.yaml")
	os.WriteFile(daemonYAML, []byte("manifests_dir: manifests\ndata_dir: mydata\n"), 0o644)

	path, err := tokensPathFor(daemonYAML)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join("mydata", "front", "tokens.json")
	if path != want {
		t.Errorf("path = %q, want %q", path, want)
	}
}

func TestParseExpiresDays(t *testing.T) {
	d, err := parseExpires("30d")
	if err != nil {
		t.Fatal(err)
	}
	if d != 30*24*time.Hour {
		t.Errorf("30d = %v, want 720h", d)
	}
}

func TestParseExpiresGoDuration(t *testing.T) {
	d, err := parseExpires("12h")
	if err != nil {
		t.Fatal(err)
	}
	if d != 12*time.Hour {
		t.Errorf("12h = %v, want 12h", d)
	}
}

// TestTokenCreateListRevoke exercises the whole offline lifecycle against
// a rig-file-shaped --config, checking the file this produces is exactly
// what daemon/internal/front/store.Tokens (the front's own reader) reads:
// 0600, the secret nowhere in it, hash-only at rest.
func TestTokenCreateListRevoke(t *testing.T) {
	stateHome := t.TempDir()
	t.Setenv("XDG_STATE_HOME", stateHome)
	rig := filepath.Join(t.TempDir(), "blender.yaml")
	os.WriteFile(rig, []byte("devices: {}\n"), 0o644)

	out := captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "ci", "--config", rig, "--scope", "read"}); err != nil {
			t.Fatal(err)
		}
	})
	secret := strings.TrimSpace(out)
	if secret == "" || !strings.HasPrefix(secret, store.TokenPrefix) {
		t.Fatalf("stdout = %q, want a bare fbt1_ secret", out)
	}

	path, err := tokensPathFor(rig)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("tokens.json mode = %v, want 0600", info.Mode().Perm())
	}
	raw, _ := os.ReadFile(path)
	if strings.Contains(string(raw), secret) {
		t.Fatal("the tokens file holds the secret in cleartext; it must hold only the hash")
	}

	// The front's own reader accepts what the CLI wrote, offline.
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	row, err := tokens.Lookup(secret)
	if err != nil {
		t.Fatalf("the front's own store refused a token the CLI just created: %v", err)
	}
	if row.Name != "ci" {
		t.Errorf("name = %q, want ci", row.Name)
	}
	tokens.Close()

	listOut := captureStdout(t, func() {
		if err := runTokenList([]string{"--config", rig}); err != nil {
			t.Fatal(err)
		}
	})
	if !strings.Contains(listOut, "ci") {
		t.Errorf("token list = %q, want it to list %q", listOut, "ci")
	}
	if strings.Contains(listOut, secret) {
		t.Fatal("token list printed the secret")
	}

	if err := runTokenRevoke([]string{row.ID, "--config", rig}); err != nil {
		t.Fatal(err)
	}
	tokens2, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer tokens2.Close()
	if _, err := tokens2.Lookup(secret); err == nil {
		t.Fatal("the revoked token is still accepted")
	}
}

func TestTokenCreateDefaultsScopeToRead(t *testing.T) {
	stateHome := t.TempDir()
	t.Setenv("XDG_STATE_HOME", stateHome)
	rig := filepath.Join(t.TempDir(), "blender.yaml")
	os.WriteFile(rig, []byte("devices: {}\n"), 0o644)

	captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "auto", "--config", rig}); err != nil {
			t.Fatal(err)
		}
	})
	path, _ := tokensPathFor(rig)
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer tokens.Close()
	list, err := tokens.List()
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 || len(list[0].Scopes) != 1 || list[0].Scopes[0] != "read:*" {
		t.Errorf("scopes = %v, want [read:*]", list)
	}
}
