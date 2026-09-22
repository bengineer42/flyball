package schema

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// TestEmbeddedProgramSchemaIsCurrent regenerates program.schema.json the same
// way regen-program.sh does (program_schema(Dialect()), called directly --
// the old `flyball program schema` Python CLI command this used to shell
// out to no longer exists, cli.py having been removed) and diffs it against
// the checked-in copy embedded above. A built-in program command added or
// changed on the Python side without re-running regen-program.sh fails this
// test.
func TestEmbeddedProgramSchemaIsCurrent(t *testing.T) {
	engineDir, err := filepath.Abs("../../../engine")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(engineDir); err != nil {
		t.Skipf("engine/ not available at %s: %v", engineDir, err)
	}
	if _, err := exec.LookPath("uv"); err != nil {
		t.Skip("uv not on PATH")
	}

	cmd := exec.Command("uv", "run", "python", "-c", `
import json
from flyball.interfaces.server.dialect import Dialect, program_schema
from flyball.model.catalog import ensure_discovered
commands = dict(ensure_discovered().commands.items())
print(json.dumps(program_schema(Dialect(commands=commands)), indent=2))
`)
	cmd.Dir = engineDir
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("regenerating schema: %v\nstderr: %s", err, stderr.String())
	}

	got := stdout.Bytes()
	want := ProgramSchemaJSON
	if !bytes.Equal(bytes.TrimSpace(got), bytes.TrimSpace(want)) {
		t.Errorf(
			"daemon/internal/schema/program.schema.json is stale relative to the "+
				"Python dialect module -- run daemon/internal/schema/regen-program.sh "+
				"and commit the result (checked-in: %d bytes, freshly generated: %d bytes)",
			len(want), len(got),
		)
	}
}
