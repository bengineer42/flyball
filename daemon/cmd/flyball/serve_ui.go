package main

import (
	"errors"
	"fmt"
	"regexp"
	"strings"

	"flyballd/internal/front"
	"flyballd/internal/frontwire"
	"flyballd/internal/rigfile"
)

// `flyball run` always starts the front (auth.md § "flyball run always
// starts the front"): the runner binds only the socket in its front-dir,
// and the front -- the embedded UI, /api/auth*, and /api, /ws, /mcp
// proxied with a signed principal -- listens where runner.front.listen
// says, 127.0.0.1:8000 by default. What it serves comes from the rig
// file's runner.front (runFront) and the run's own flags (parseRunArgs).

// runOpts are `flyball run`'s own flags, taken out of the runner's.
type runOpts struct {
	listen       string // --listen, or its alias --serve-ui; "" = the rig file's
	uv           bool   // --uv
	insecureOpen bool   // --insecure-open or FLYBALL_INSECURE_OPEN
	rest         []string
}

// parseRunArgs takes --listen/--serve-ui ADDR (either form, `--x ADDR` or
// `--x=ADDR`; --listen wins over --serve-ui), --uv and --insecure-open out
// of args, anywhere; the rest, rig files first, go to flyball-runner.
func parseRunArgs(args []string) runOpts {
	var o runOpts
	var serveUI string
	for i := 0; i < len(args); i++ {
		a := args[i]
		name, value, hasValue := strings.Cut(a, "=")
		switch name {
		case "--listen", "--serve-ui":
			if !hasValue {
				if i+1 >= len(args) {
					o.rest = append(o.rest, a)
					continue
				}
				i++
				value = args[i]
			}
			if name == "--listen" {
				o.listen = value
			} else {
				serveUI = value
			}
			continue
		}
		switch a {
		case "--uv":
			o.uv = true
		case "--insecure-open":
			o.insecureOpen = true
		default:
			o.rest = append(o.rest, a)
		}
	}
	if o.listen == "" {
		o.listen = serveUI
	}
	o.insecureOpen = o.insecureOpen || frontwire.InsecureOpenEnv()
	return o
}

// runFront is the front's configuration for `flyball run` from the rig
// file's runner section: runner.front, else runner.run (accepted for one
// release, with a warning: serve_ui is listen, uv is uv; its port means
// nothing now). listen, when not "", is --listen and beats both. bad is
// why the block cannot be read; cfg.Listen still says where it asked to
// listen, for the fallback (frontwire.Plan).
func runFront(runner map[string]any, listen string) (cfg front.Config, useUV bool, bad error, warnings []string) {
	block, hasFront := runner["front"]
	legacy, hasRun := runner["run"]
	switch {
	case hasFront:
		if hasRun {
			warnings = append(warnings, "runner.run is ignored: runner.front is set")
		}
		m, ok := block.(map[string]any)
		if !ok && block != nil {
			bad = fmt.Errorf("runner.front is not a mapping")
			break
		}
		m = clone(m)
		if v, ok := m["uv"]; ok {
			useUV, ok = v.(bool)
			if !ok {
				bad = fmt.Errorf("runner.front.uv: %v is not true or false", v)
			}
			delete(m, "uv")
		}
		if bad == nil {
			cfg, bad = frontwire.Decode(m)
		}
		if bad != nil {
			cfg.Listen, _ = m["listen"].(string)
			bad = fmt.Errorf("runner.front: %w", bad)
		}
	case hasRun:
		warnings = append(warnings, "runner.run is deprecated: use runner.front (serve_ui is now listen, uv is uv; the runner no longer takes a port)")
		m, ok := legacy.(map[string]any)
		if !ok && legacy != nil {
			bad = fmt.Errorf("runner.run is not a mapping")
			break
		}
		if v, ok := m["serve_ui"]; ok && v != nil {
			if cfg.Listen, ok = v.(string); !ok {
				bad = fmt.Errorf("runner.run.serve_ui: %v is not an address", v)
			}
		}
		if v, ok := m["uv"]; ok && v != nil {
			if useUV, ok = v.(bool); !ok {
				bad = fmt.Errorf("runner.run.uv: %v is not true or false", v)
			}
		}
	}
	if listen != "" {
		cfg.Listen = listen
	}
	return cfg, useUV, bad, warnings
}

func clone(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

// rigDocument is the document the runner builds from files (later
// overlaying earlier) and sets (--set KEY=VALUE, applied last), `extends`
// resolved: rigfile.ResolveLayers, as `flyball rig check` resolves it
// (D-046). err is why it cannot be loaded: the caller's front then falls
// back (D-028) rather than serve the local shape as if the files had no
// runner.front; flyball-runner reports the error properly once it loads
// them for real.
func rigDocument(files, sets []string) (map[string]any, error) {
	document, _, err := rigfile.ResolveLayers(files, sets)
	if err != nil {
		return nil, err
	}
	return document, nil
}

// argparseNegative is argparse's own test for an argument that looks like
// a negative number (_negative_number_matcher): with no such option
// defined, flyball-runner's parser takes one as a positional -- a rig file.
var argparseNegative = regexp.MustCompile(`^-\d+$|^-\d*\.\d+$`)

// runLayers is which of the runner's arguments (runOpts.rest) are rig
// files and which are --set expressions, as flyball-runner's argparse
// reads them: its `rig` (nargs "*") takes exactly the leading arguments
// that do not start with "-" (a bare word after a flag is that flag's
// value, or an error the runner exits 2 on), and --set X / --set=X
// append (--set has no abbreviation: --se is --session or --store too).
// `--` and a negative-number-shaped argument are refused: argparse takes
// what follows `--`, and a "-1", as rig files, where this rule would not.
func runLayers(args []string) (files, sets []string, err error) {
	for _, a := range args {
		switch {
		case a == "--":
			return nil, nil, errors.New("flyball run: `--` is refused: every rig file goes first, before any flag")
		case argparseNegative.MatchString(a):
			return nil, nil, fmt.Errorf("flyball run: %q is refused: flyball-runner would take it as a rig file", a)
		}
	}
	k := 0
	for k < len(args) && !strings.HasPrefix(args[k], "-") {
		k++
	}
	files = args[:k]
	for i := k; i < len(args); i++ {
		if v, ok := strings.CutPrefix(args[i], "--set="); ok {
			sets = append(sets, v)
		} else if args[i] == "--set" && i+1 < len(args) {
			i++
			sets = append(sets, args[i])
		}
	}
	return files, sets, nil
}
