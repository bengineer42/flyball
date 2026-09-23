package rigfile

import "testing"

// These three fixtures are each schema-valid on their own -- proving the
// error, when it fires, comes from CheckBusinessRules and not from
// schema.ValidateRig (see the package doc on checks.go for the exact
// Python source each mirrors).

func TestCheckUndeclaredLink(t *testing.T) {
	doc := map[string]any{
		"links": map[string]any{"chamber": map[string]any{}},
		"devices": map[string]any{
			"thermocouple": map[string]any{
				"link": "nonexistent",
			},
		},
	}
	err := CheckBusinessRules(doc)
	if err == nil {
		t.Fatal("expected an undeclared-link error, got nil")
	}
	if got := err.Error(); got == "" {
		t.Fatal("expected a non-empty error message")
	}
}

func TestCheckUndeclaredLink_declaredIsFine(t *testing.T) {
	doc := map[string]any{
		"links": map[string]any{"chamber": map[string]any{}},
		"devices": map[string]any{
			"thermocouple": map[string]any{
				"link": "chamber",
			},
		},
	}
	if err := CheckBusinessRules(doc); err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
}

func TestCheckSingleDefaultController(t *testing.T) {
	doc := map[string]any{
		"controllers": map[string]any{
			"a.x": map[string]any{"default": true},
			"b.y": map[string]any{"default": true},
		},
	}
	err := CheckBusinessRules(doc)
	if err == nil {
		t.Fatal("expected a single-default-controller error, got nil")
	}
}

func TestCheckSingleDefaultController_oneIsFine(t *testing.T) {
	doc := map[string]any{
		"controllers": map[string]any{
			"a.x": map[string]any{"default": true},
			"b.y": map[string]any{"default": false},
		},
	}
	if err := CheckBusinessRules(doc); err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
}

func TestCheckClockOnlySimulated(t *testing.T) {
	doc := map[string]any{
		"clock": map[string]any{"speed": 2.0},
		"links": map[string]any{"bus": map[string]any{"type": "serial"}},
	}
	err := CheckBusinessRules(doc)
	if err == nil {
		t.Fatal("expected a clock/simulated error, got nil")
	}
}

func TestCheckClockOnlySimulated_simIsFine(t *testing.T) {
	for _, typ := range []string{"sim_plant", "fake_registers"} {
		doc := map[string]any{
			"clock": map[string]any{"speed": 2.0},
			"links": map[string]any{"bus": map[string]any{"type": typ}},
		}
		if err := CheckBusinessRules(doc); err != nil {
			t.Fatalf("type %s: expected no error, got %v", typ, err)
		}
	}
}

func TestCheckClockOnlySimulated_noClockIsFine(t *testing.T) {
	doc := map[string]any{
		"links": map[string]any{"bus": map[string]any{"type": "serial"}},
	}
	if err := CheckBusinessRules(doc); err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
}
