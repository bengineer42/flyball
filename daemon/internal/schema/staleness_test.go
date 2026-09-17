package schema

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// TestEmbeddedSchemaIsCurrent is the CI-equivalent staleness check: it
// regenerates rig.schema.json into a temp file via the real
// `uv run flyball rig schema` invocation (the same one regen.sh uses) and
// diffs it against the checked-in copy embedded above. A driver added or
// changed on the Python side without re-running regen.sh fails this test.
func TestEmbeddedSchemaIsCurrent(t *testing.T) {
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

	cmd := exec.Command("uv", "run", "flyball", "rig", "schema")
	cmd.Dir = engineDir
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("regenerating schema: %v\nstderr: %s", err, stderr.String())
	}

	got := stdout.Bytes()
	want := RigSchemaJSON
	if !bytes.Equal(bytes.TrimSpace(got), bytes.TrimSpace(want)) {
		t.Errorf(
			"daemon/internal/schema/rig.schema.json is stale relative to the Python "+
				"RigConfig model -- run daemon/internal/schema/regen.sh and commit the "+
				"result (checked-in: %d bytes, freshly generated: %d bytes)",
			len(want), len(got),
		)
	}
}
