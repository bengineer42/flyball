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

	if !strings.Contains(out, "run RIG-FILE [--listen ADDR] [--uv] [--insecure-open]") {
		t.Errorf("usage() run line is stale: %q", out)
	}
	if strings.Contains(out, "--serve-ui ADDR] [--uv] [flyball-runner") {
		t.Errorf("usage() still advertises --serve-ui as run's only listen flag")
	}
	if !strings.Contains(out, "login [URL] [--scope SCOPE]") {
		t.Errorf("usage() login line doesn't mention --scope: %q", out)
	}
}
