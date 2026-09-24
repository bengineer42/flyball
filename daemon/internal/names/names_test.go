package names

import "testing"

func TestTheGrammarAndTheCanonicalSpelling(t *testing.T) {
	for _, name := range []string{"oven", "humidity-sim", "humidity_single_sensor", "a1-b2"} {
		if !Valid(name) {
			t.Errorf("%q: want valid", name)
		}
	}
	for _, name := range []string{"", "1st", "Dry", "dry pump", "a.b", "x/y", "-a", string(make([]byte, 65))} {
		if Valid(name) {
			t.Errorf("%q: want refused", name)
		}
	}
	if Canonical("humidity-single-sensor") != "humidity_single_sensor" || Canonical("oven") != "oven" {
		t.Error("canonical: - is _")
	}
}
