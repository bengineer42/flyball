// Local `rig` operations against the rig file itself, not a running
// runner -- so, like `run`/`daemon`/`logs`, dispatched before target
// resolution. Only `rig schema` lives here for now: it prints the
// embedded JSON Schema (daemon/internal/schema/rig.schema.json, kept
// current from the Python RigConfig model by regen.sh). `rig check`'s
// hand-written validation rules (undeclared-link references, single-
// default-controller count, clock/simulated) are a separate follow-up,
// not implemented here.
package main

import (
	"fmt"
	"os"

	"flyballd/internal/schema"
)

func runRigCommand(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: flyball rig schema")
	}
	switch args[0] {
	case "schema":
		_, err := os.Stdout.Write(schema.RigSchemaJSON)
		return err
	default:
		return fmt.Errorf("unknown rig command %q; only `rig schema` is implemented so far", args[0])
	}
}
