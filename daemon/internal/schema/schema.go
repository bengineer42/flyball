// Package schema embeds the rig file's JSON Schema -- generated from the
// Python side's own RigConfig model (engine/src/flyball/runtime/config.py's
// rig_schema()), which is the single source of truth for every driver/type
// registered there. The daemon/CLI never derives this schema itself; it
// only carries the checked-in copy (rig.schema.json, kept current by
// regen.sh) and validates against it.
package schema

import (
	"bytes"
	_ "embed"
	"fmt"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// RigSchemaJSON is the raw bytes of the checked-in rig file schema, exactly
// as `flyball rig schema` (the Python CLI) emits it.
//
//go:embed rig.schema.json
var RigSchemaJSON []byte

// ProgramSchemaJSON is the raw bytes of the checked-in program file schema,
// exactly as `flyball program schema` (the Python CLI) emits it. Built from
// the static Commands registry (engine/src/flyball/programmer/command.py),
// populated by Step subclasses as their modules import -- not plugin
// discovery -- so this schema is genuinely static, unlike the rig schema's
// driver set.
//
//go:embed program.schema.json
var ProgramSchemaJSON []byte

// RigSchema compiles the embedded schema into a validator, once.
func RigSchema() (*jsonschema.Schema, error) {
	doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(RigSchemaJSON))
	if err != nil {
		return nil, fmt.Errorf("decoding embedded rig schema: %w", err)
	}
	c := jsonschema.NewCompiler()
	if err := c.AddResource("rig.schema.json", doc); err != nil {
		return nil, fmt.Errorf("adding embedded rig schema as a resource: %w", err)
	}
	sch, err := c.Compile("rig.schema.json")
	if err != nil {
		return nil, fmt.Errorf("compiling embedded rig schema: %w", err)
	}
	return sch, nil
}

// ValidateRig validates a decoded rig document (as produced by
// jsonschema.UnmarshalJSON, or any JSON-shaped map[string]any/[]any/
// scalar tree -- e.g. a YAML rig file first decoded to that shape) against
// the embedded rig schema.
func ValidateRig(doc any) error {
	sch, err := RigSchema()
	if err != nil {
		return err
	}
	return sch.Validate(doc)
}

// ProgramSchema compiles the embedded program schema into a validator, once.
func ProgramSchema() (*jsonschema.Schema, error) {
	doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(ProgramSchemaJSON))
	if err != nil {
		return nil, fmt.Errorf("decoding embedded program schema: %w", err)
	}
	c := jsonschema.NewCompiler()
	if err := c.AddResource("program.schema.json", doc); err != nil {
		return nil, fmt.Errorf("adding embedded program schema as a resource: %w", err)
	}
	sch, err := c.Compile("program.schema.json")
	if err != nil {
		return nil, fmt.Errorf("compiling embedded program schema: %w", err)
	}
	return sch, nil
}

// ValidateProgram validates a decoded program document against the
// embedded program schema.
func ValidateProgram(doc any) error {
	sch, err := ProgramSchema()
	if err != nil {
		return err
	}
	return sch.Validate(doc)
}
