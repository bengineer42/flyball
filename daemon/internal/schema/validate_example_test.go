package schema

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"gopkg.in/yaml.v3"
)

// exampleRigs are the repository's own rig files that stand alone: no
// extends, and no board: (`flyball rig check` does not apply board
// profiles, so extensions/linux/examples/greenhouse.yaml and sim.yaml are
// left out).
var exampleRigs = []string{
	"examples/simulated/*.yaml",
	"examples/scenarios/*/rig.yaml",
	"extensions/linux/examples/sensors/*.yaml",
}

// TestValidatesTheExampleRigs proves the embedded schema is usable end to
// end and holds every first-party driver: each example rig -- simulated,
// chip and linux drivers alike -- validates cleanly.
func TestValidatesTheExampleRigs(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	var paths []string
	for _, pattern := range exampleRigs {
		matches, err := filepath.Glob(filepath.Join(root, pattern))
		if err != nil {
			t.Fatal(err)
		}
		if len(matches) == 0 {
			t.Errorf("no example rigs match %s", pattern)
		}
		paths = append(paths, matches...)
	}
	for _, path := range paths {
		rel, _ := filepath.Rel(root, path)
		t.Run(rel, func(t *testing.T) {
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			var yamlDoc any
			if err := yaml.Unmarshal(raw, &yamlDoc); err != nil {
				t.Fatalf("parsing as YAML: %v", err)
			}
			if m, ok := yamlDoc.(map[string]any); ok && (m["extends"] != nil || m["board"] != nil) {
				t.Skip("layered or board rig: not a standalone document")
			}
			// yaml.v3 decodes mappings as map[string]any, so a JSON
			// round-trip gives the plain any-tree the validator wants.
			jsonBytes, err := json.Marshal(yamlDoc)
			if err != nil {
				t.Fatal(err)
			}
			var doc any
			if err := json.Unmarshal(jsonBytes, &doc); err != nil {
				t.Fatal(err)
			}
			if err := ValidateRig(doc); err != nil {
				t.Errorf("failed schema validation: %v", err)
			}
		})
	}
}
