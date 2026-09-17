// Local `rig` operations against the rig file itself, not a running
// runner -- so, like `run`/`daemon`/`logs`, dispatched before target
// resolution. `rig schema` prints the embedded JSON Schema
// (daemon/internal/schema/rig.schema.json, kept current from the Python
// RigConfig model by regen.sh). `rig check` loads one or more layered rig
// files (extends resolved, --set applied), validates the merged document
// against that same embedded schema, then runs the hand-written
// cross-field checks the schema can't express
// (daemon/internal/rigfile/checks.go), matching
// engine/src/flyball/cli.py's cmd_rig_check.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"flyballd/internal/rigfile"
	"flyballd/internal/schema"
)

func runRigCommand(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: flyball rig check|schema ...")
	}
	switch args[0] {
	case "schema":
		_, err := os.Stdout.Write(schema.RigSchemaJSON)
		return err
	case "check":
		return runRigCheck(args[1:])
	default:
		return fmt.Errorf("unknown rig command %q; use check or schema", args[0])
	}
}

func runRigCheck(args []string) error {
	var paths []string
	var sets []string
	print := false
	i := 0
	for i < len(args) {
		switch args[i] {
		case "--set":
			if i+1 >= len(args) {
				return fmt.Errorf("--set needs a KEY=VALUE argument")
			}
			sets = append(sets, args[i+1])
			i += 2
		case "--print":
			print = true
			i++
		default:
			paths = append(paths, args[i])
			i++
		}
	}
	if len(paths) == 0 {
		return fmt.Errorf("usage: flyball rig check PATH [PATH ...] [--set KEY=VALUE] [--print]")
	}

	document, files, err := rigfile.ResolveLayers(paths, sets)
	if err != nil {
		return fmt.Errorf("%s: %w", strings.Join(paths, ", "), err)
	}

	if err := schema.ValidateRig(document); err != nil {
		return fmt.Errorf("%s: %w", strings.Join(paths, ", "), err)
	}
	if err := rigfile.CheckBusinessRules(document); err != nil {
		return fmt.Errorf("%s: %w", strings.Join(paths, ", "), err)
	}

	name, _ := document["name"].(string)
	if name == "" {
		name = "unnamed"
	}
	links := len(asMapAny(document["links"]))
	devices := len(asMapAny(document["devices"]))
	controllers := len(asMapAny(document["controllers"]))
	summary := fmt.Sprintf("%s: ok -- %s: %d links, %d devices, %d controllers",
		strings.Join(paths, ", "), name, links, devices, controllers)
	if len(files) > 1 {
		summary += fmt.Sprintf("; from %d files", len(files))
	}
	fmt.Println(summary)

	if print {
		out, err := rigfile.Dumps(document, filepath.Ext(paths[0]))
		if err != nil {
			return err
		}
		fmt.Print(out)
	}
	return nil
}

func asMapAny(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}
