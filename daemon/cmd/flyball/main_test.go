package main

import (
	"bytes"
	"strings"
	"testing"
)

// TestUsageMatchesTheFlagsRunAndLoginActuallyTake: two C1 follow-ups --
// usage() still said `run`'s old --serve-ui-only shape and said nothing
// of `login`'s --scope.
func TestUsageMatchesTheFlagsRunAndLoginActuallyTake(t *testing.T) {
	var buf bytes.Buffer
	usage(&buf)
	out := buf.String()

	if !strings.Contains(out, "run RIG-FILE [RIG-FILE ...] [--listen ADDR] [--uv] [--insecure-open] [--set KEY=VALUE ...]") {
		t.Errorf("usage() run line is stale: %q", out)
	}
	if strings.Contains(out, "--serve-ui ADDR] [--uv] [flyball-runner") {
		t.Errorf("usage() still advertises --serve-ui as run's only listen flag")
	}
	if !strings.Contains(out, "login [URL] [--scope SCOPE]") {
		t.Errorf("usage() login line doesn't mention --scope: %q", out)
	}
}

// TestUsagePasswordNamesWhereTheHashGoes: the bare runner has no password
// login (runner.auth.password is removed); the hash goes to a front, in
// runner.front.password or flyballd.yaml's password:.
func TestUsagePasswordNamesWhereTheHashGoes(t *testing.T) {
	var buf bytes.Buffer
	usage(&buf)
	out := buf.String()
	if strings.Contains(out, "runner.auth.password") {
		t.Errorf("usage() still sends the hash to runner.auth.password")
	}
	if !strings.Contains(out, "runner.front.password") || !strings.Contains(out, "flyballd.yaml") {
		t.Errorf("usage() password line does not name runner.front.password and flyballd.yaml's password:")
	}
}
