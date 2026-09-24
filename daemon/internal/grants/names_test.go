package grants

import (
	"slices"
	"testing"
)

// A scope names a rig by its key: `-` and `_` are one (D-079), and a name
// that is not a key is refused.
func TestAScopeNamesARigByEitherSpelling(t *testing.T) {
	s, err := ParseScope("read:humidity-sim")
	if err != nil || s.Rig != "humidity_sim" {
		t.Fatalf("read:humidity-sim: %+v, %v; want the rig humidity_sim", s, err)
	}
	for _, rig := range []string{"humidity-sim", "humidity_sim"} {
		if !slices.Contains(ForRig([]string{"read:humidity-sim"}, rig), "read") {
			t.Errorf("read:humidity-sim on %q: want read", rig)
		}
	}
	for _, bad := range []string{"read:Oven", "read:a.b", "read:1st"} {
		if _, err := ParseScope(bad); err == nil {
			t.Errorf("%s: want refused", bad)
		}
	}
}
