package main

import (
	"reflect"
	"strings"
	"testing"
)

// `flyball run`'s own flags come out of the runner's; the rest go on.
func TestParseRunArgs(t *testing.T) {
	for name, c := range map[string]struct {
		args   []string
		listen string
		uv     bool
		open   bool
		rest   []string
	}{
		"bare":       {[]string{"rig.yaml"}, "", false, false, []string{"rig.yaml"}},
		"listen":     {[]string{"rig.yaml", "--listen", ":9000"}, ":9000", false, false, []string{"rig.yaml"}},
		"serve-ui":   {[]string{"--serve-ui", ":9000", "rig.yaml"}, ":9000", false, false, []string{"rig.yaml"}},
		"both":       {[]string{"rig.yaml", "--serve-ui", ":1", "--listen", ":2"}, ":2", false, false, []string{"rig.yaml"}},
		"uv, open":   {[]string{"rig.yaml", "--uv", "--insecure-open"}, "", true, true, []string{"rig.yaml"}},
		"runner's":   {[]string{"rig.yaml", "--port", "9001", "--record"}, "", false, false, []string{"rig.yaml", "--port", "9001", "--record"}},
		"eq listen":  {[]string{"rig.yaml", "--listen=:9000"}, ":9000", false, false, []string{"rig.yaml"}},
		"eq serveui": {[]string{"rig.yaml", "--serve-ui=:9000"}, ":9000", false, false, []string{"rig.yaml"}},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("FLYBALL_INSECURE_OPEN", "")
			o := parseRunArgs(c.args)
			if o.listen != c.listen || o.uv != c.uv || o.insecureOpen != c.open || !reflect.DeepEqual(o.rest, c.rest) {
				t.Fatalf("got %+v, want listen %q uv %v open %v rest %v", o, c.listen, c.uv, c.open, c.rest)
			}
		})
	}
	t.Setenv("FLYBALL_INSECURE_OPEN", "1")
	if !parseRunArgs([]string{"rig.yaml"}).insecureOpen {
		t.Fatal("FLYBALL_INSECURE_OPEN=1 is the opt-in too")
	}
}

// runner.front is the front's block; runner.run is read for one release,
// with a warning; --listen beats both.
func TestRunFront(t *testing.T) {
	front := map[string]any{"listen": "0.0.0.0:8443", "auth": "password", "uv": true}
	run := map[string]any{"serve_ui": ":8123", "uv": true, "port": 9000}

	cfg, uv, bad, warn := runFront(nil, "")
	if cfg.Listen != "" || uv || bad != nil || len(warn) != 0 {
		t.Fatalf("no runner section: %+v %v %v %v", cfg, uv, bad, warn)
	}

	cfg, uv, bad, warn = runFront(map[string]any{"front": front}, "")
	if cfg.Listen != "0.0.0.0:8443" || cfg.Auth != "password" || !uv || bad != nil || len(warn) != 0 {
		t.Fatalf("runner.front: %+v %v %v %v", cfg, uv, bad, warn)
	}

	cfg, uv, bad, warn = runFront(map[string]any{"run": run}, "")
	if cfg.Listen != ":8123" || !uv || bad != nil || len(warn) != 1 || !strings.Contains(warn[0], "runner.run") {
		t.Fatalf("runner.run: %+v %v %v %v", cfg, uv, bad, warn)
	}

	cfg, _, _, warn = runFront(map[string]any{"front": front, "run": run}, "")
	if cfg.Listen != "0.0.0.0:8443" || len(warn) != 1 || !strings.Contains(warn[0], "ignored") {
		t.Fatalf("both: %+v %v", cfg, warn)
	}

	cfg, _, _, _ = runFront(map[string]any{"front": front}, "127.0.0.1:9999")
	if cfg.Listen != "127.0.0.1:9999" {
		t.Fatalf("--listen: %+v", cfg)
	}
}

// A runner.front that cannot be read is an error for the D-028 fallback,
// which keeps the port asked for.
func TestRunFrontBad(t *testing.T) {
	for name, runner := range map[string]map[string]any{
		"type":       {"front": map[string]any{"listen": "0.0.0.0:8443", "auth": []any{"x"}}},
		"unknown":    {"front": map[string]any{"listen": "0.0.0.0:8443", "passwd": "x"}},
		"not a map":  {"front": "0.0.0.0:8443"},
		"bad run":    {"run": map[string]any{"serve_ui": []any{1}}},
		"bad run uv": {"run": map[string]any{"uv": "yes"}},
	} {
		t.Run(name, func(t *testing.T) {
			cfg, _, bad, _ := runFront(runner, "")
			if bad == nil {
				t.Fatalf("no error: %+v", cfg)
			}
			if name == "type" || name == "unknown" {
				if cfg.Listen != "0.0.0.0:8443" {
					t.Fatalf("listen %q, want the one asked for kept for the fallback", cfg.Listen)
				}
			}
		})
	}
}
