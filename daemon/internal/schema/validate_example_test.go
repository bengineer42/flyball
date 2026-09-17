package schema

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

// TestValidatesRealExampleRig proves the embedded schema is genuinely
// usable end-to-end: load a real rig file (YAML, as rig files normally
// are), convert it to the plain JSON-shaped value the validator wants,
// and check it validates cleanly against the embedded schema.
func TestValidatesRealExampleRig(t *testing.T) {
	path, err := filepath.Abs("../../../examples/simulated/oven.yaml")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("example rig not available at %s: %v", path, err)
	}

	var yamlDoc any
	if err := yaml.Unmarshal(raw, &yamlDoc); err != nil {
		t.Fatalf("parsing %s as YAML: %v", path, err)
	}

	// yaml.v3 already decodes mappings as map[string]interface{} (unlike
	// v2's map[interface{}]interface{}), so a JSON round-trip is enough
	// to get the plain any-tree jsonschema.Validate expects.
	jsonBytes, err := json.Marshal(yamlDoc)
	if err != nil {
		t.Fatalf("re-marshalling %s to JSON: %v", path, err)
	}
	var doc any
	if err := json.Unmarshal(jsonBytes, &doc); err != nil {
		t.Fatalf("decoding %s as JSON: %v", path, err)
	}

	if err := ValidateRig(doc); err != nil {
		t.Errorf("real example rig %s failed schema validation: %v", path, err)
	}
}
