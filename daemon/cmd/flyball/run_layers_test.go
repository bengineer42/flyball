package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// --- D-046: the front reads every rig file and --set the runner reads -----

// TestRunLayersSplit: the rig files are argparse's `rig` (nargs "*"):
// the leading arguments that do not start with "-"; every --set X and
// --set=X is a set. `--` and a negative-number-shaped argument (argparse
// takes both as positional) are refused.
func TestRunLayersSplit(t *testing.T) {
	for _, c := range []struct {
		args        []string
		files, sets []string
		refused     bool
	}{
		{args: []string{"a"}, files: []string{"a"}},
		{args: []string{"a", "b", "--record"}, files: []string{"a", "b"}},
		{args: []string{"a", "--drivers", "d"}, files: []string{"a"}},
		{args: []string{"a", "--set=runner.front.auth=password"}, files: []string{"a"}, sets: []string{"runner.front.auth=password"}},
		{args: []string{"a", "b", "--set", "x=1", "--set=y=2"}, files: []string{"a", "b"}, sets: []string{"x=1", "y=2"}},
		{args: []string{"a", "--set", "runner.front.listen=x", "b"}, files: []string{"a"}, sets: []string{"runner.front.listen=x"}},
		{args: []string{"a", "--", "b"}, refused: true},
		{args: []string{"a", "-1"}, refused: true},
		{args: []string{"a", "-.5"}, refused: true},
		{args: []string{"a", "--store", "-2.5"}, refused: true},
	} {
		files, sets, err := runLayers(c.args)
		if c.refused {
			if err == nil {
				t.Errorf("runLayers(%q) = %q, %q; want refused", c.args, files, sets)
			}
			continue
		}
		if err != nil || !slices.Equal(files, c.files) || !slices.Equal(sets, c.sets) {
			t.Errorf("runLayers(%q) = %q, %q, %v; want %q, %q", c.args, files, sets, err, c.files, c.sets)
		}
	}
}

// TestRunReadsASecondFilesFront: `flyball run a.yaml b.yaml`, b carrying
// runner.front.auth: password -- the front serves the password door, as
// the runner (which merges both) expects.
func TestRunReadsASecondFilesFront(t *testing.T) {
	dir := fakeEnv(t)
	b := filepath.Join(dir, "b.yaml")
	os.WriteFile(b, []byte("runner:\n  front:\n    auth: password\n    password: \""+extendsTestHash+"\"\n"), 0o644)
	r := startRunIn(t, dir, "name: t\nrunner:\n  front:\n    listen: 127.0.0.1:0\n", b)
	if auth := authAt(t, r, r.addr()); auth["shape"] != "password" {
		t.Fatalf("/api/auth shape = %v, want password (b.yaml's runner.front); output:\n%s", auth["shape"], r.output())
	}
}

// TestRunReadsASetOnTheFront: `--set runner.front.auth=password` (with the
// password set too) reaches the front.
func TestRunReadsASetOnTheFront(t *testing.T) {
	dir := fakeEnv(t)
	r := startRunIn(t, dir, "name: t\nrunner:\n  front:\n    listen: 127.0.0.1:0\n",
		"--set", "runner.front.auth=password", "--set=runner.front.password="+extendsTestHash)
	if auth := authAt(t, r, r.addr()); auth["shape"] != "password" {
		t.Fatalf("/api/auth shape = %v, want password (the --set); output:\n%s", auth["shape"], r.output())
	}
}

// TestRunSetNameNamesTheFrontsRig: `a b --set name=x` -- the front's rig
// is x, the name the runner gives it.
func TestRunSetNameNamesTheFrontsRig(t *testing.T) {
	dir := fakeEnv(t)
	b := filepath.Join(dir, "b.yaml")
	os.WriteFile(b, []byte("name: b\n"), 0o644)
	r := startRunIn(t, dir, "name: a\nrunner:\n  front:\n    listen: 127.0.0.1:0\n", b, "--set", "name=x")
	r.addr()
	for end := time.Now().Add(5 * time.Second); time.Now().Before(end) && !strings.Contains(r.output(), "serving rig "); time.Sleep(20 * time.Millisecond) {
	}
	if !strings.Contains(r.output(), "serving rig x ") {
		t.Fatalf("the front does not serve rig x:\n%s", r.output())
	}
}

// TestRunRefusesDoubleDash: `flyball run a -- b` is refused before
// anything starts: argparse would take b as a rig file, the front would not.
func TestRunRefusesDoubleDash(t *testing.T) {
	dir := fakeEnv(t)
	a := filepath.Join(dir, "a.yaml")
	os.WriteFile(a, []byte("name: a\nrunner:\n  front:\n    listen: 127.0.0.1:0\n"), 0o644)
	done := make(chan error, 1)
	go func() { done <- run([]string{a, "--", filepath.Join(dir, "b.yaml")}, make(chan os.Signal)) }()
	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "--") {
			t.Fatalf("run a -- b: err = %v, want a refusal naming --", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("run a -- b started a rig")
	}
	if lines := readLines(filepath.Join(dir, "args")); len(lines) != 0 {
		t.Fatalf("a runner was spawned: %q", lines)
	}
}
