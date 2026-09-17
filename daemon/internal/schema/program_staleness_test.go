package schema

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// TestEmbeddedProgramSchemaIsCurrent regenerates program.schema.json via the
// real `uv run flyball program schema` invocation (the same one
// regen-program.sh uses) and diffs it against the checked-in copy embedded
// above. A built-in program command added or changed on the Python side
// without re-running regen-program.sh fails this test.
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

	cmd := exec.Command("uv", "run", "flyball", "program", "schema")
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
