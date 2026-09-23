package grants

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

// The embedded file is the one WP0 committed, parsed as it is on disk.
func TestVocabularyIsTheFile(t *testing.T) {
	raw, err := os.ReadFile("vocabulary.json")
	if err != nil {
		t.Fatal(err)
	}
	var want Vocabulary
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatal(err)
	}
	if got := Vocab(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Vocab() = %+v, want %+v", got, want)
	}
	if got := Verbs(); !reflect.DeepEqual(got, []string{"operate", "read"}) {
		t.Fatalf("Verbs() = %v", got)
	}
	if Management() != "manage" {
		t.Fatalf("Management() = %q", Management())
	}
}

func TestExpand(t *testing.T) {
	for role, want := range map[string][]string{
		"all":    {"operate", "read"},
		"viewer": {"read"},
		"nobody": nil,
		"":       nil,
	} {
		if got := Expand(role); !reflect.DeepEqual(got, want) {
			t.Errorf("Expand(%q) = %v, want %v", role, got, want)
		}
	}
	// The caller cannot change the table through the slice it gets.
	Expand("all")[0] = "hacked"
	if Expand("all")[0] != "operate" {
		t.Fatal("Expand returned the table's own slice")
	}
}

func TestParseScope(t *testing.T) {
	ok := map[string]string{
		"read:*":       "read:*",
		"read":         "read:*", // a bare verb is every rig
		"operate:rig1": "operate:rig1",
		"manage":       "manage",
	}
	for in, want := range ok {
		s, err := ParseScope(in)
		if err != nil || s.String() != want {
			t.Errorf("ParseScope(%q) = %v, %v; want %s", in, s, err, want)
		}
	}
	for _, bad := range []string{"", "fly:*", "read:", ":rig", "manage:*", "read:a b", "read:a\nb", "admin", "READ:*"} {
		if s, err := ParseScope(bad); err == nil {
			t.Errorf("ParseScope(%q) = %v, want an error", bad, s)
		}
	}
}

func TestNormalizeScopes(t *testing.T) {
	got, err := NormalizeScopes([]string{"read", "operate:b", "read:*", "manage"})
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"manage", "operate:b", "read:*"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("NormalizeScopes = %v, want %v", got, want)
	}
	if _, err := NormalizeScopes([]string{"read", "nope"}); err == nil {
		t.Fatal("an unknown verb was accepted")
	}
	got, _ = NormalizeScopes(nil)
	if got == nil || len(got) != 0 {
		t.Fatalf("NormalizeScopes(nil) = %#v, want an empty slice", got)
	}
}

func TestForRig(t *testing.T) {
	scopes := []string{"read:*", "operate:blender", "manage", "operate:other", "garbage"}
	if got := ForRig(scopes, "blender"); !reflect.DeepEqual(got, []string{"operate", "read"}) {
		t.Errorf("blender: %v", got)
	}
	if got := ForRig(scopes, "furnace"); !reflect.DeepEqual(got, []string{"read"}) {
		t.Errorf("furnace: %v", got)
	}
	// Never nil: the principal's scp is always an array.
	if got := ForRig(nil, "x"); got == nil || len(got) != 0 {
		t.Errorf("empty: %#v", got)
	}
	// A verb the vocabulary does not know never reaches a principal.
	if got := ForRig([]string{"admin:*"}, "x"); len(got) != 0 {
		t.Errorf("unknown verb: %v", got)
	}
}

func TestIntersect(t *testing.T) {
	got := Intersect([]string{"read", "operate", "read"}, []string{"read"})
	if !reflect.DeepEqual(got, []string{"read"}) {
		t.Fatalf("Intersect = %v", got)
	}
	if got := Intersect(nil, []string{"read"}); got == nil || len(got) != 0 {
		t.Fatalf("Intersect(nil) = %#v", got)
	}
}

func TestAll(t *testing.T) {
	if got := All(); !reflect.DeepEqual(got, []string{"operate:*", "read:*"}) {
		t.Fatalf("All() = %v", got)
	}
}

// A proxy user who matches no grant gets read (Ben, 23 Sep); a matched
// subject or group gets the role's verbs; an unknown role gives nothing
// beyond the default.
func TestMatch(t *testing.T) {
	g := map[string][]string{
		"all":     {"ben", "group:lab-admins"},
		"viewer":  {"group:visitors"},
		"unknown": {"eve"},
	}
	cases := []struct {
		subject string
		groups  []string
		want    []string
	}{
		{"ben", nil, []string{"operate:*", "read:*"}},
		{"alice", []string{"lab-admins"}, []string{"operate:*", "read:*"}},
		{"carol", []string{"visitors"}, []string{"read:*"}},
		{"dave", nil, []string{"read:*"}},
		{"eve", nil, []string{"read:*"}},
		// group: is only a group, never a subject named "group:x".
		{"group:lab-admins", nil, []string{"read:*"}},
	}
	for _, c := range cases {
		if got := Match(g, c.subject, c.groups); !reflect.DeepEqual(got, c.want) {
			t.Errorf("Match(%q, %v) = %v, want %v", c.subject, c.groups, got, c.want)
		}
	}
	if bad := UnknownRoles(g); !reflect.DeepEqual(bad, []string{"unknown"}) {
		t.Errorf("UnknownRoles = %v", bad)
	}
}

func TestHasManagement(t *testing.T) {
	if !HasManagement([]string{"read:*", "manage"}) || HasManagement([]string{"read:*"}) {
		t.Fatal("HasManagement")
	}
}
