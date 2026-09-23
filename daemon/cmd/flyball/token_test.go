package main

import (
	"os"
	"path/filepath"
	"slices"
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

	path, err := tokensPathFor(rig, false)
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

	path, err := tokensPathFor(daemonYAML, false)
	if err != nil {
		t.Fatal(err)
	}
	// frontwire.DaemonDir resolves data_dir against the process's cwd, the
	// same way flyballd itself does (cmd/flyballd/main.go), so this and a
	// running flyballd --config flyballd.yaml agree whatever directory
	// each is run from -- not the (relative, so cwd-fragile) path this
	// used to return.
	want := filepath.Join(mustAbs(t, "mydata"), "front", "tokens.json")
	if path != want {
		t.Errorf("path = %q, want %q", path, want)
	}
}

// TestTokensPathForDaemonConfigWithoutManifestsDir: none of flyballd.yaml's
// own keys is required (DefaultDaemonConfig fills them in), so a file
// naming only data_dir must still be recognised as a daemon config, not
// mistaken for a rig file.
func TestTokensPathForDaemonConfigWithoutManifestsDir(t *testing.T) {
	dir := t.TempDir()
	daemonYAML := filepath.Join(dir, "flyballd.yaml")
	os.WriteFile(daemonYAML, []byte("data_dir: mydata\n"), 0o644)

	path, err := tokensPathFor(daemonYAML, false)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(mustAbs(t, "mydata"), "front", "tokens.json")
	if path != want {
		t.Errorf("path = %q, want %q", path, want)
	}
}

func mustAbs(t *testing.T, p string) string {
	t.Helper()
	abs, err := filepath.Abs(p)
	if err != nil {
		t.Fatal(err)
	}
	return abs
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

	path, err := tokensPathFor(rig, false)
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
	path, _ := tokensPathFor(rig, false)
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

// TestTokenCreateElevatedScopeAddsRead: `--scope operate` alone would get
// 403 "needs 'read'" on a websocket, since the vocabulary is an unordered
// set of verbs until D-034 lands (auth-d1's finding). The CLI adds read
// on the same rig(s) whenever a non-read scope is requested.
func TestTokenCreateElevatedScopeAddsRead(t *testing.T) {
	stateHome := t.TempDir()
	t.Setenv("XDG_STATE_HOME", stateHome)
	rig := filepath.Join(t.TempDir(), "blender.yaml")
	os.WriteFile(rig, []byte("devices: {}\n"), 0o644)

	captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "op", "--config", rig, "--scope", "operate:blender"}); err != nil {
			t.Fatal(err)
		}
	})
	path, _ := tokensPathFor(rig, false)
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer tokens.Close()
	list, err := tokens.List()
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"operate:blender", "read:blender"}
	if len(list) != 1 || !slices.Equal(list[0].Scopes, want) {
		t.Errorf("scopes = %v, want %v", list, want)
	}
}

// TestTokenCreateHonoursRigFileTokensConfig: `flyball token create --config
// blender.yaml` applies blender.yaml's `runner.front.tokens` block the same
// way a running front would -- a request with no --expires gets the
// configured default, and a request above the configured max is clamped.
func TestTokenCreateHonoursRigFileTokensConfig(t *testing.T) {
	stateHome := t.TempDir()
	t.Setenv("XDG_STATE_HOME", stateHome)
	rig := filepath.Join(t.TempDir(), "blender.yaml")
	os.WriteFile(rig, []byte("devices: {}\nrunner:\n  front:\n    tokens:\n      default_lifetime: 5d\n      max_lifetime: 20d\n"), 0o644)

	captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "no-expires", "--config", rig}); err != nil {
			t.Fatal(err)
		}
	})
	captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "above-max", "--config", rig, "--expires", "100d"}); err != nil {
			t.Fatal(err)
		}
	})

	path, err := tokensPathFor(rig, false)
	if err != nil {
		t.Fatal(err)
	}
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer tokens.Close()
	list, err := tokens.List()
	if err != nil {
		t.Fatal(err)
	}
	byName := map[string]store.Token{}
	for _, tok := range list {
		byName[tok.Name] = tok
	}
	if got := byName["no-expires"].Expires.Sub(byName["no-expires"].Created); got != 5*24*time.Hour {
		t.Errorf("no --expires: lifetime %v, want the configured default 5d", got)
	}
	if got := byName["above-max"].Expires.Sub(byName["above-max"].Created); got != 20*24*time.Hour {
		t.Errorf("--expires 100d, above the configured max: lifetime %v, want clamped to 20d", got)
	}
}

// TestTokenCreateFallsBackOnBadTokensConfig: a `max_lifetime` above the
// built-in ceiling falls back (D-028: it never refuses to create), with a
// warning on stderr.
func TestTokenCreateFallsBackOnBadTokensConfig(t *testing.T) {
	stateHome := t.TempDir()
	t.Setenv("XDG_STATE_HOME", stateHome)
	rig := filepath.Join(t.TempDir(), "blender.yaml")
	os.WriteFile(rig, []byte("devices: {}\nrunner:\n  front:\n    tokens:\n      max_lifetime: 400d\n"), 0o644)

	var stderr strings.Builder
	origStderr := os.Stderr
	r, w, _ := os.Pipe()
	os.Stderr = w
	captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "x", "--config", rig}); err != nil {
			t.Fatal(err)
		}
	})
	w.Close()
	os.Stderr = origStderr
	buf := make([]byte, 4096)
	n, _ := r.Read(buf)
	stderr.Write(buf[:n])
	if !strings.Contains(stderr.String(), "max_lifetime") {
		t.Errorf("stderr = %q, want a max_lifetime warning", stderr.String())
	}

	path, err := tokensPathFor(rig, false)
	if err != nil {
		t.Fatal(err)
	}
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	defer tokens.Close()
	list, err := tokens.List()
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 || list[0].Expires.Sub(list[0].Created) != store.TokenLifetimeDefault {
		t.Errorf("tokens = %+v, want one token at the built-in default lifetime", list)
	}
}

// TestTokensPathForFrontOnlyFlyballdYAML: a flyballd.yaml that sets only
// the front's keys (listen, auth, password) has none of the daemon's own
// keys, and was taken for a rig file: the token went into a `flyball run`
// front-dir no front reads. A file named flyballd.yaml is the daemon's.
func TestTokensPathForFrontOnlyFlyballdYAML(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	daemonYAML := filepath.Join(t.TempDir(), "flyballd.yaml")
	os.WriteFile(daemonYAML, []byte("listen: 0.0.0.0:9443\nauth: password\npassword: $scrypt$x\n"), 0o644)

	path, err := tokensPathFor(daemonYAML, false)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(mustAbs(t, "data"), "front", "tokens.json")
	if path != want {
		t.Errorf("path = %q, want %q (the daemon's default data_dir)", path, want)
	}
}

// TestTokenDaemonFlag: a daemon config under another name, with only the
// front's keys, is the daemon's when --daemon says so, for create, list
// and revoke alike.
func TestTokenDaemonFlag(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	dir := t.TempDir()
	conf := filepath.Join(dir, "front.yaml")
	os.WriteFile(conf, []byte("listen: 0.0.0.0:9443\nauth: password\n"), 0o644)
	t.Chdir(dir)

	if path, _ := tokensPathFor(conf, false); strings.HasPrefix(path, dir) {
		t.Fatalf("without --daemon a front-only file under another name is a rig file; got %q", path)
	}
	out := captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "ci", "--config", conf, "--daemon"}); err != nil {
			t.Fatal(err)
		}
	})
	if strings.TrimSpace(out) == "" {
		t.Fatal("no secret printed")
	}
	want := filepath.Join(dir, "data", "front", "tokens.json")
	if _, err := os.Stat(want); err != nil {
		t.Fatalf("--daemon did not write the daemon's tokens file %s: %v", want, err)
	}
	listed := captureStdout(t, func() {
		if err := runTokenList([]string{"--config", conf, "--daemon"}); err != nil {
			t.Fatal(err)
		}
	})
	if !strings.Contains(listed, "ci") {
		t.Errorf("token list --daemon: %q", listed)
	}
}
