package main

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"flyballd/internal/api"
	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/endpoint"
	"flyballd/internal/front"
	"flyballd/internal/front/store"
	"flyballd/internal/registry"
)

// rigsBackend is flyballd's backend for these tests: each runner a
// fakeStopRunner (a real socket, answering POST /api/rig/stop with a
// report when the principal holds operate), status overridable, and the
// runners it was asked to stop recorded.
type rigsBackend struct {
	t       *testing.T
	mu      sync.Mutex
	runners map[string]*fakeStopRunner
	status  map[string]backend.Status
	stopped []string
}

func (b *rigsBackend) Start(name string, spec backend.Spec) (string, error) {
	fr := &fakeStopRunner{aud: name, ep: endpoint.Endpoint{Network: "unix", Address: filepath.Join(b.t.TempDir(), "sock")}}
	for i := range fr.key {
		fr.key[i] = byte(i + 3)
	}
	ln, err := net.Listen("unix", fr.ep.Address)
	if err != nil {
		return "", err
	}
	// The front forwards the un-stripped path, as to a runner with --root-path.
	srv := &http.Server{Handler: http.StripPrefix(spec.RootPath, http.HandlerFunc(fr.serve))}
	go srv.Serve(ln)
	b.t.Cleanup(func() { srv.Close() })
	b.mu.Lock()
	defer b.mu.Unlock()
	b.runners[name] = fr
	return fr.ep.String(), nil
}

func (b *rigsBackend) Stop(name string) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, ok := b.runners[name]; !ok {
		return fmt.Errorf("no runner named %q", name)
	}
	delete(b.runners, name)
	b.stopped = append(b.stopped, name)
	return nil
}

func (b *rigsBackend) Restart(string) error { return nil }

func (b *rigsBackend) Logs(string) (io.ReadCloser, error) {
	return io.NopCloser(strings.NewReader("")), nil
}

func (b *rigsBackend) Status(name string) (backend.Status, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if _, ok := b.runners[name]; !ok {
		return backend.StatusStopped, nil
	}
	if st, ok := b.status[name]; ok {
		return st, nil
	}
	return backend.StatusRunning, nil
}

func (b *rigsBackend) Channel(name string) (backend.Channel, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	fr, ok := b.runners[name]
	if !ok {
		return backend.Channel{}, fmt.Errorf("no runner named %q", name)
	}
	return backend.Channel{Endpoint: fr.ep, Aud: fr.aud, Key: fr.key}, nil
}

func (b *rigsBackend) stoppedNames() []string {
	b.mu.Lock()
	defer b.mu.Unlock()
	out := append([]string(nil), b.stopped...)
	sort.Strings(out)
	return out
}

// rigsDaemon is flyballd's front (api.NewFront) over rigs oven and kiln,
// in the password shape, at FLYBALLD_URL.
type rigsDaemon struct {
	be     *rigsBackend
	reg    *registry.Registry
	tokens *store.Tokens
}

func newRigsDaemon(t *testing.T) *rigsDaemon {
	t.Helper()
	be := &rigsBackend{t: t, runners: map[string]*fakeStopRunner{}, status: map[string]backend.Status{}}
	reg := registry.New(be)
	plan := front.Resolve(front.Config{Auth: front.ShapePassword, Password: scryptLineForTest(t)}, false)
	tokensPath := filepath.Join(t.TempDir(), "tokens.json")
	f := api.NewFront(reg, front.Options{Plan: plan, TokensPath: tokensPath, FailDelay: time.Millisecond})
	srv := httptest.NewServer(f)
	t.Cleanup(func() { srv.CloseClientConnections(); srv.Close(); f.Close(); plan.Close() })
	for _, name := range []string{"oven", "kiln"} {
		if err := reg.Start(config.Manifest{Name: name, ServerConfig: name + ".yaml", Host: "127.0.0.1", RootPath: "/" + name}); err != nil {
			t.Fatal(err)
		}
	}
	tokens, err := store.OpenTokens(tokensPath, store.TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { tokens.Close() })
	t.Setenv("FLYBALLD_URL", srv.URL)
	t.Setenv("FLYBALL_URL", "http://127.0.0.1:1")
	t.Setenv("FLYBALLD_TOKEN", "")
	t.Setenv("FLYBALL_TOKEN", "")
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	return &rigsDaemon{be: be, reg: reg, tokens: tokens}
}

func (d *rigsDaemon) token(t *testing.T, scopes ...string) string {
	t.Helper()
	secret, _, err := d.tokens.Create(store.NewToken{Name: fmt.Sprint("t", time.Now().UnixNano()), Scopes: scopes})
	if err != nil {
		t.Fatal(err)
	}
	return secret
}

// `flyball stop --all` is the rig stop on every rig flyballd lists, with
// operate on each: every rig's report is printed, and the runners go on.
func TestStopAllStopsEveryRig(t *testing.T) {
	d := newRigsDaemon(t)
	op := d.token(t, "operate")
	var err error
	out := captureStdout(t, func() { err = runStopCommand("", op, []string{"--all", "--reason", "maintenance"}) })
	if err != nil {
		t.Fatalf("stop --all: %v\n%s", err, out)
	}
	for _, rig := range []string{"kiln", "oven"} {
		if !strings.Contains(out, rig+":") {
			t.Errorf("no report for %s:\n%s", rig, out)
		}
	}
	if strings.Count(out, "\nstopped: ") != 2 || !strings.Contains(out, "program interrupted") {
		t.Errorf("want two stop reports:\n%s", out)
	}
	if s := d.be.stoppedNames(); len(s) != 0 {
		t.Errorf("stop --all ended runner processes %v; it is the rig stop only", s)
	}
}

// A rig whose stop is refused (read, not operate) or fails (not running)
// makes `flyball stop --all` exit non-zero, naming it; the others are
// still stopped and reported.
func TestStopAllReportsEachRefusal(t *testing.T) {
	d := newRigsDaemon(t)
	tok := d.token(t, "operate:kiln", "read:oven")
	var err error
	out := captureStdout(t, func() { err = runStopCommand("", tok, []string{"--all"}) })
	if err == nil || !strings.Contains(err.Error(), "oven") || strings.Contains(err.Error(), "kiln") {
		t.Fatalf("stop --all with operate on kiln only: %v, want an error naming oven alone\n%s", err, out)
	}
	if !strings.Contains(out, "kiln:") || strings.Count(out, "\nstopped: ") != 1 {
		t.Errorf("kiln's report missing:\n%s", out)
	}

	d.be.mu.Lock()
	d.be.status["kiln"] = backend.StatusBusy
	d.be.mu.Unlock()
	op := d.token(t, "operate")
	out = captureStdout(t, func() { err = runStopCommand("", op, []string{"--all"}) })
	if err == nil || !strings.Contains(err.Error(), "kiln") {
		t.Fatalf("stop --all with kiln busy: %v, want an error naming kiln\n%s", err, out)
	}
}

// No rig visible to the credential is an error, never a silent success:
// an operator must not believe everything stopped.
func TestStopAllWithNothingVisibleIsAnError(t *testing.T) {
	newRigsDaemon(t)
	if err := runStopCommand("", "", []string{"--all"}); err == nil || !strings.Contains(err.Error(), "nothing was stopped") {
		t.Fatalf("stop --all anonymous: %v", err)
	}
	t.Setenv("FLYBALLD_URL", "http://127.0.0.1:1")
	if err := runStopCommand("", "", []string{"--all"}); err == nil || !strings.Contains(err.Error(), "--pid") {
		t.Fatalf("stop --all with flyballd unreachable: %v, want the break-glass hint", err)
	}
	if err := runStopCommand("", "", []string{"--all", "oven"}); err == nil {
		t.Fatal("stop --all NAME accepted")
	}
}

// `flyball runners stop --all` ends every runner process through
// flyballd's management API: a token with the manage scope, or nothing
// is stopped.
func TestRunnersStopAllNeedsManage(t *testing.T) {
	d := newRigsDaemon(t)
	op := d.token(t, "operate")
	var err error
	out := captureStdout(t, func() { err = runRunnersCommand(op, []string{"stop", "--all"}) })
	if err == nil || !strings.Contains(err.Error(), "manage") {
		t.Fatalf("runners stop --all with an operate token: %v, want a refusal naming manage\n%s", err, out)
	}
	if s := d.be.stoppedNames(); len(s) != 0 {
		t.Fatalf("runners stopped without manage: %v", s)
	}

	t.Setenv("FLYBALLD_TOKEN", d.token(t, "manage"))
	out = captureStdout(t, func() { err = runRunnersCommand("", []string{"stop", "--all"}) })
	if err != nil {
		t.Fatalf("runners stop --all with FLYBALLD_TOKEN manage: %v\n%s", err, out)
	}
	if s := strings.Join(d.be.stoppedNames(), ","); s != "kiln,oven" {
		t.Errorf("stopped %q, want kiln,oven\n%s", s, out)
	}
	for _, rig := range []string{"kiln", "oven"} {
		if !strings.Contains(out, rig+": stopped") {
			t.Errorf("no line for %s:\n%s", rig, out)
		}
	}
}

// `flyball runners stop NAME` ends that one runner only.
func TestRunnersStopName(t *testing.T) {
	d := newRigsDaemon(t)
	manage := d.token(t, "manage")
	var err error
	captureStdout(t, func() { err = runRunnersCommand(manage, []string{"stop", "kiln"}) })
	if err != nil {
		t.Fatal(err)
	}
	if s := strings.Join(d.be.stoppedNames(), ","); s != "kiln" {
		t.Errorf("stopped %q, want kiln", s)
	}
	captureStdout(t, func() { err = runRunnersCommand(manage, []string{"stop", "nosuch"}) })
	if err == nil {
		t.Error("runners stop of an unknown runner succeeded")
	}
	for _, args := range [][]string{{"stop"}, {"stop", "--all", "kiln"}, {}, {"start"}} {
		if err := runRunnersCommand(manage, args); err == nil {
			t.Errorf("runners %q accepted", args)
		}
	}
}
