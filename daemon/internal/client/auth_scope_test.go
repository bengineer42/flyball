package client

import (
	"context"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"flyballd/internal/front"
)

// newRootedTestFront is newTestFront with the rig served at root
// "/blender" instead of "/" -- the shape a flyballd path gives a rig
// (front.go's rel := strings.TrimPrefix(path, rig.Root)), so a Target
// with Prefix "/blender" actually routes here, unlike a bare Prefix
// tacked onto a "/"-rooted rig.
func newRootedTestFront(t *testing.T, er *echoRunner) *httptest.Server {
	t.Helper()
	plan := front.Resolve(front.Config{Auth: "password", Password: scryptLine(testPassword, 16)}, false)
	opts := front.Options{
		Plan: plan,
		Route: front.SingleRig(front.Rig{Root: "/blender", Name: "blender", Target: func(context.Context) (front.Target, error) {
			return er.target(), nil
		}}),
		TokensPath: filepath.Join(t.TempDir(), "front", "tokens.json"),
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
	return srv
}

// TestLoginScopeOperateBecomesRigScoped is D-036 safeguard 2: a bare
// `--scope operate` is not every rig, only the one logged in to. A
// daemon-routed Target (Prefix "/blender") names it without any network
// round trip -- SingleRig ignores the path in this test harness, so the
// prefix alone proves the rewrite, independent of routing.
func TestLoginScopeOperateBecomesRigScoped(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv := newRootedTestFront(t, er)

	target := Target{BaseURL: srv.URL, Prefix: "/blender"}
	tok, err := Login(target, testPassword, LoginOptions{Scopes: []string{"operate"}})
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if want := []string{"operate:blender", "read:blender"}; !slices.Equal(tok.Scopes, want) {
		t.Errorf("scopes = %v, want %v", tok.Scopes, want)
	}
	if !tok.Elevated {
		t.Errorf("Elevated = false for an operate scope")
	}
}

// TestLoginScopeBareOperateFetchesRigFromAuthInfo: the Pi case -- a
// `flyball run`-style front reached at a bare URL, no `/<name>` in the
// path and no -s NAME to give a Prefix. bareVerbRig now falls back to
// GET .../api/auth's "rig" field (added to front/auth.go's AuthInfo for
// this) rather than refusing outright.
func TestLoginScopeBareOperateFetchesRigFromAuthInfo(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, _ := newTestFront(t, er) // SingleRig, Name "blender", root "/"

	target := Target{BaseURL: srv.URL} // no Prefix, no path: the bare-URL case
	tok, err := Login(target, testPassword, LoginOptions{Scopes: []string{"operate"}})
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if want := []string{"operate:blender", "read:blender"}; !slices.Equal(tok.Scopes, want) {
		t.Errorf("scopes = %v, want %v", tok.Scopes, want)
	}
}

// TestLoginScopeOperateStarIsKept: `operate:*` must never be widened or
// narrowed -- it is only ever used when spelled out (safeguard 2).
func TestLoginScopeOperateStarIsKept(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, _ := newTestFront(t, er)

	target := Target{BaseURL: srv.URL}
	tok, err := Login(target, testPassword, LoginOptions{Scopes: []string{"operate:*"}})
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if want := []string{"operate:*", "read:*"}; !slices.Equal(tok.Scopes, want) {
		t.Errorf("scopes = %v, want %v", tok.Scopes, want)
	}
	if !tok.Elevated {
		t.Errorf("Elevated = false for operate:*")
	}
}

// TestLoginScopeBareOperateUnknownRigRefused: no Prefix and no path in
// the base URL, so the rig cannot be determined -- refuse the bare verb
// rather than guess (safeguard 2), before ever touching the network (the
// target isn't even listening).
func TestLoginScopeBareOperateUnknownRigRefused(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	target := Target{BaseURL: "http://127.0.0.1:1"} // nothing listens here
	_, err := Login(target, testPassword, LoginOptions{Scopes: []string{"operate"}})
	if err == nil {
		t.Fatal("expected an error for a bare operate scope against an unnamed rig")
	}
	if !strings.Contains(err.Error(), "operate:<rig>") || !strings.Contains(err.Error(), "operate:*") {
		t.Errorf("error %q should point at both operate:<rig> and operate:*", err.Error())
	}
}

// TestLoginScopeManageRefused: `manage` can only be minted by `flyball
// token create` on the host, never through `flyball login --scope`.
func TestLoginScopeManageRefused(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, _ := newTestFront(t, er)

	target := Target{BaseURL: srv.URL}
	_, err := Login(target, testPassword, LoginOptions{Scopes: []string{"manage"}})
	if err == nil {
		t.Fatal("expected an error for --scope manage")
	}
	if !strings.Contains(err.Error(), "token create") {
		t.Errorf("error %q should point at `flyball token create --scope manage`", err.Error())
	}
}

// TestLoginScopeOperateExpiresWithin30Days is safeguard 3: an
// operate-or-above token flyball login mints lives at most 30 days,
// whatever the front's own config would otherwise allow.
func TestLoginScopeOperateExpiresWithin30Days(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv := newRootedTestFront(t, er)

	before := time.Now()
	target := Target{BaseURL: srv.URL, Prefix: "/blender"}
	tok, err := Login(target, testPassword, LoginOptions{Scopes: []string{"operate"}})
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if max := before.Add(31 * 24 * time.Hour); tok.Expires.After(max) {
		t.Errorf("expires %s is more than 30 days out", tok.Expires)
	}
}

// TestLoginTokenNameShape: `cli:<user>@<host>` (safeguard 4), sanitised
// to a name the store accepts.
func TestLoginTokenNameShape(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv, _ := newTestFront(t, er)

	target := Target{BaseURL: srv.URL}
	tok, err := Login(target, testPassword, LoginOptions{})
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if !strings.HasPrefix(tok.Name, "cli:") || !strings.Contains(tok.Name, "@") {
		t.Errorf("name = %q, want cli:<user>@<host>", tok.Name)
	}
	if len(tok.Name) == 0 || len(tok.Name) > 64 {
		t.Errorf("name length = %d, want 1-64", len(tok.Name))
	}
}

// TestLoginSavesTokenFileMode0600: the whole point of LoggedIn.Path is
// the safeguard-1 warning naming this file.
func TestLoginSavesTokenFileMode0600(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	er := newEchoRunner(t, "run-test")
	srv := newRootedTestFront(t, er)

	target := Target{BaseURL: srv.URL, Prefix: "/blender"}
	tok, err := Login(target, testPassword, LoginOptions{Scopes: []string{"operate"}})
	if err != nil {
		t.Fatalf("Login: %v", err)
	}
	if !tok.Elevated {
		t.Fatal("expected Elevated for an operate scope")
	}
	if tok.Path == "" {
		t.Fatal("LoggedIn.Path is empty")
	}
	fi, err := os.Stat(tok.Path)
	if err != nil {
		t.Fatalf("stat %s: %v", tok.Path, err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("mode = %v, want 0600", fi.Mode().Perm())
	}
	want, err := tokenFilePath(loginKey(target))
	if err != nil {
		t.Fatal(err)
	}
	if tok.Path != want {
		t.Errorf("Path = %s, want %s", tok.Path, want)
	}
}
