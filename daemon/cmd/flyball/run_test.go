package main

import (
	"os"
	"reflect"
	"testing"
)

// TestResolveRunFlagsNoYAMLDefaults checks the bare, no-`runner.run` case
// behaves exactly as before this change: no UI, default port, no uv.
func TestResolveRunFlagsNoYAMLDefaults(t *testing.T) {
	serveAddr, wantUI, port, useUV, rest := resolveRunFlags([]string{"rig.yaml"}, nil, "")
	if wantUI || serveAddr != "" || port != "8000" || useUV {
		t.Fatalf("got (%q, %v, %q, %v), want (\"\", false, \"8000\", false)", serveAddr, wantUI, port, useUV)
	}
	if !reflect.DeepEqual(rest, []string{"rig.yaml"}) {
		t.Fatalf("rest = %v, want [rig.yaml]", rest)
	}
}

// TestResolveRunFlagsYAMLSetsDefaults checks that runner.run's serve_ui/uv
// take effect when the equivalent CLI flag is absent.
func TestResolveRunFlagsYAMLSetsDefaults(t *testing.T) {
	defaults := map[string]any{"serve_ui": ":8123", "uv": true}
	serveAddr, wantUI, port, useUV, rest := resolveRunFlags([]string{"rig.yaml"}, defaults, "")
	if !wantUI || serveAddr != ":8123" || port != "8000" || !useUV {
		t.Fatalf("got (%q, %v, %q, %v), want (\":8123\", true, \"8000\", true)", serveAddr, wantUI, port, useUV)
	}
	if !reflect.DeepEqual(rest, []string{"rig.yaml"}) {
		t.Fatalf("rest = %v, want [rig.yaml]", rest)
	}
}

// TestResolveRunFlagsCLIWinsOverYAML checks that an explicit CLI flag beats
// a runner.run default set to something else -- "YAML sets the default,
// CLI overrides", not a merge.
func TestResolveRunFlagsCLIWinsOverYAML(t *testing.T) {
	defaults := map[string]any{"serve_ui": ":8123", "uv": true, "port": "9999"}
	args := []string{"rig.yaml", "--serve-ui", ":9000", "--port", "9001"}
	serveAddr, wantUI, port, useUV, rest := resolveRunFlags(args, defaults, "")
	if !wantUI || serveAddr != ":9000" || port != "9001" {
		t.Fatalf("got (%q, %v, %q), want (\":9000\", true, \"9001\")", serveAddr, wantUI, port)
	}
	if !useUV {
		t.Fatal("uv: YAML default should still apply since --uv wasn't given")
	}
	if !reflect.DeepEqual(rest, []string{"rig.yaml", "--port", "9001"}) {
		t.Fatalf("rest = %v, want [rig.yaml --port 9001]: the runner serves where the front proxies", rest)
	}
}

// TestResolveRunFlagsYAMLPortNumber checks a YAML port given as a bare
// number (not a string) is still usable as the flag's string form.
func TestResolveRunFlagsYAMLPortNumber(t *testing.T) {
	defaults := map[string]any{"port": 9500}
	_, _, port, _, rest := resolveRunFlags([]string{"rig.yaml"}, defaults, "")
	if port != "9500" || !reflect.DeepEqual(rest, []string{"rig.yaml", "--port", "9500"}) {
		t.Fatalf("port = %q, rest %v, want 9500 and --port 9500 passed on", port, rest)
	}
}

// TestResolveRunFlagsRunnerPort checks the proxy follows the rig file's own
// runner.port when no --port is given, and leaves the runner's args alone.
func TestResolveRunFlagsRunnerPort(t *testing.T) {
	_, _, port, _, rest := resolveRunFlags([]string{"rig.yaml"}, map[string]any{"port": "9500"}, "8123")
	if port != "8123" || !reflect.DeepEqual(rest, []string{"rig.yaml"}) {
		t.Fatalf("port = %q, rest %v, want 8123 and nothing added", port, rest)
	}
	_, _, port, _, _ = resolveRunFlags([]string{"rig.yaml", "--port", "9001"}, nil, "8123")
	if port != "9001" {
		t.Fatalf("port = %q, want --port to win", port)
	}
}

// TestResolveRunFlagsEmptyServeUIIsNotSet checks that an empty-string
// serve_ui in the YAML (as opposed to it being absent) does not turn the
// UI on, matching the "non-empty string means serve" rule.
func TestResolveRunFlagsEmptyServeUIIsNotSet(t *testing.T) {
	defaults := map[string]any{"serve_ui": ""}
	_, wantUI, _, _, _ := resolveRunFlags([]string{"rig.yaml"}, defaults, "")
	if wantUI {
		t.Fatal("empty serve_ui should not turn the UI on")
	}
}

// TestRunYAMLDefaultsReadsRunnerRun checks the file-loading half separately
// against a real rig file on disk, including extends resolution.
func TestRunYAMLDefaultsReadsRunnerRun(t *testing.T) {
	dir := t.TempDir()
	rigPath := dir + "/rig.yaml"
	if err := writeFile(rigPath, "name: t\nrunner:\n  run:\n    serve_ui: \":8123\"\n    uv: true\n"); err != nil {
		t.Fatal(err)
	}
	got := runYAMLDefaults(rigPath)
	want := map[string]any{"serve_ui": ":8123", "uv": true}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("runYAMLDefaults = %#v, want %#v", got, want)
	}
}

// TestRunYAMLDefaultsAbsent checks the tolerant, no-runner.run-at-all case
// (most rigs) returns nil rather than erroring.
func TestRunYAMLDefaultsAbsent(t *testing.T) {
	dir := t.TempDir()
	rigPath := dir + "/rig.yaml"
	if err := writeFile(rigPath, "name: t\n"); err != nil {
		t.Fatal(err)
	}
	if got := runYAMLDefaults(rigPath); got != nil {
		t.Fatalf("runYAMLDefaults = %#v, want nil", got)
	}
}

func writeFile(path, contents string) error {
	return os.WriteFile(path, []byte(contents), 0o644)
}
