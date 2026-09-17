// `program schema` -- local, like `rig schema`: prints the embedded JSON
// Schema for program files (daemon/internal/schema/program.schema.json),
// generated from engine/src/flyball/server/dialect.py's program_schema()
// over the static Commands registry. No runner involved.
package main

import (
	"fmt"
	"os"

	"flyballd/internal/schema"
)

func runProgramSchemaCommand(args []string) error {
	if len(args) != 0 {
		return fmt.Errorf("usage: flyball program schema")
	}
	_, err := os.Stdout.Write(schema.ProgramSchemaJSON)
	return err
}
