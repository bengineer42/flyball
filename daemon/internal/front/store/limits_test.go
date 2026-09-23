package store

import (
	"strings"
	"testing"
	"time"
)

func TestParseDurationDays(t *testing.T) {
	d, err := ParseDuration("90d")
	if err != nil {
		t.Fatal(err)
	}
	if d != 90*24*time.Hour {
		t.Errorf("90d = %v, want 2160h", d)
	}
}

func TestParseDurationGoSyntax(t *testing.T) {
	d, err := ParseDuration("36h")
	if err != nil {
		t.Fatal(err)
	}
	if d != 36*time.Hour {
		t.Errorf("36h = %v, want 36h", d)
	}
}

func TestParseDurationBad(t *testing.T) {
	if _, err := ParseDuration("banana"); err == nil {
		t.Error("banana: want an error")
	}
}

func TestResolveLifetimesUnset(t *testing.T) {
	lt, warnings := ResolveLifetimes("", "")
	if lt != DefaultLifetimes() {
		t.Errorf("lt = %+v, want the built-ins %+v", lt, DefaultLifetimes())
	}
	if len(warnings) != 0 {
		t.Errorf("warnings = %v, want none", warnings)
	}
}

func TestResolveLifetimesMaxTightens(t *testing.T) {
	lt, warnings := ResolveLifetimes("", "200d")
	if lt.Max != 200*24*time.Hour {
		t.Errorf("max = %v, want 200d", lt.Max)
	}
	if lt.Default != TokenLifetimeDefault {
		t.Errorf("default = %v, want the built-in %v", lt.Default, TokenLifetimeDefault)
	}
	if len(warnings) != 0 {
		t.Errorf("warnings = %v, want none", warnings)
	}
}

func TestResolveLifetimesMaxAboveBuiltinFallsBack(t *testing.T) {
	lt, warnings := ResolveLifetimes("", "400d")
	if lt.Max != TokenLifetimeMax {
		t.Errorf("max = %v, want the built-in ceiling %v", lt.Max, TokenLifetimeMax)
	}
	if len(warnings) != 1 || !strings.Contains(warnings[0], "max_lifetime") {
		t.Fatalf("warnings = %v, want one naming max_lifetime", warnings)
	}
}

func TestResolveLifetimesMaxInvalidOrNonPositiveFallsBack(t *testing.T) {
	for _, bad := range []string{"banana", "0d", "-5d"} {
		lt, warnings := ResolveLifetimes("", bad)
		if lt.Max != TokenLifetimeMax {
			t.Errorf("max_lifetime %q: max = %v, want the built-in %v", bad, lt.Max, TokenLifetimeMax)
		}
		if len(warnings) != 1 {
			t.Errorf("max_lifetime %q: warnings = %v, want one", bad, warnings)
		}
	}
}

func TestResolveLifetimesDefaultAboveMaxFallsBack(t *testing.T) {
	lt, warnings := ResolveLifetimes("100d", "50d")
	if lt.Max != 50*24*time.Hour {
		t.Errorf("max = %v, want 50d", lt.Max)
	}
	if lt.Default != TokenLifetimeDefault {
		t.Errorf("default = %v, want the built-in %v (fell back, not clamped to max)", lt.Default, TokenLifetimeDefault)
	}
	if len(warnings) != 1 || !strings.Contains(warnings[0], "default_lifetime") {
		t.Fatalf("warnings = %v, want one naming default_lifetime", warnings)
	}
}

func TestResolveLifetimesBothValid(t *testing.T) {
	lt, warnings := ResolveLifetimes("30d", "60d")
	if lt.Default != 30*24*time.Hour || lt.Max != 60*24*time.Hour {
		t.Errorf("lt = %+v, want default 30d max 60d", lt)
	}
	if len(warnings) != 0 {
		t.Errorf("warnings = %v, want none", warnings)
	}
}
