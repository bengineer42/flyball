package rigfile

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestMerge_dictsDeepMergeOthersReplace(t *testing.T) {
	base := map[string]any{
		"a": 1,
		"b": map[string]any{"x": 1, "y": 2},
		"c": []any{1, 2},
	}
	overlay := map[string]any{
		"a": 2,
		"b": map[string]any{"y": 3, "z": 4},
		"c": []any{9},
	}
	got := Merge(base, overlay)
	want := map[string]any{
		"a": 2,
		"b": map[string]any{"x": 1, "y": 3, "z": 4},
		"c": []any{9},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v, want %#v", got, want)
	}
}

func TestMerge_nilDeletesKey(t *testing.T) {
	base := map[string]any{"a": 1, "b": 2}
	overlay := map[string]any{"a": nil}
	got := Merge(base, overlay)
	if _, ok := got["a"]; ok {
		t.Fatalf("expected key 'a' deleted, got %#v", got)
	}
	if got["b"] != 2 {
		t.Fatalf("expected 'b' untouched, got %#v", got)
	}
}

func TestParseSet(t *testing.T) {
	cases := []struct {
		expr string
		path []string
		val  any
	}{
		{"devices.furnace.config.noise=0.3", []string{"devices", "furnace", "config", "noise"}, 0.3},
		{"a.b=true", []string{"a", "b"}, true},
		{"a.b=null", []string{"a", "b"}, nil},
		{"a=hello", []string{"a"}, "hello"},
	}
	for _, c := range cases {
		path, val, err := ParseSet(c.expr)
		if err != nil {
			t.Fatalf("%s: %v", c.expr, err)
		}
		if !reflect.DeepEqual(path, c.path) {
			t.Errorf("%s: path got %v want %v", c.expr, path, c.path)
		}
		if !reflect.DeepEqual(val, c.val) {
			t.Errorf("%s: value got %#v want %#v", c.expr, val, c.val)
		}
	}
}

func TestParseSet_noEquals(t *testing.T) {
	if _, _, err := ParseSet("nokeyvalue"); err == nil {
		t.Fatal("expected an error for a missing '='")
	}
}

func TestApplySet_setsAndDeletes(t *testing.T) {
	doc := map[string]any{"devices": map[string]any{"furnace": map[string]any{"config": map[string]any{"noise": 0.1}}}}
	out, err := ApplySet(doc, []string{"devices", "furnace", "config", "noise"}, 0.3)
	if err != nil {
		t.Fatal(err)
	}
	got := out["devices"].(map[string]any)["furnace"].(map[string]any)["config"].(map[string]any)["noise"]
	if got != 0.3 {
		t.Fatalf("got %v", got)
	}

	out2, err := ApplySet(out, []string{"devices", "furnace", "config", "noise"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	config := out2["devices"].(map[string]any)["furnace"].(map[string]any)["config"].(map[string]any)
	if _, ok := config["noise"]; ok {
		t.Fatalf("expected noise deleted, got %#v", config)
	}
}

func TestApplySet_deleteMissingPathIsNoop(t *testing.T) {
	doc := map[string]any{"a": 1}
	out, err := ApplySet(doc, []string{"nope", "deep"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(out, doc) {
		t.Fatalf("expected an unchanged document, got %#v", out)
	}
}

func TestResolveLayers_extendsAndLaterFileWins(t *testing.T) {
	dir := t.TempDir()
	base := filepath.Join(dir, "base.yaml")
	mid := filepath.Join(dir, "mid.yaml")
	top := filepath.Join(dir, "top.yaml")
	mustWrite(t, base, "name: base\nlinks:\n  chamber:\n    tag: sim_plant\n")
	mustWrite(t, mid, "extends: [base.yaml]\nname: mid\n")
	mustWrite(t, top, "name: top\n")

	doc, files, err := ResolveLayers([]string{mid, top}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if doc["name"] != "top" {
		t.Fatalf("expected the command-line-last file to win, got name=%v", doc["name"])
	}
	links := doc["links"].(map[string]any)
	if _, ok := links["chamber"]; !ok {
		t.Fatalf("expected 'chamber' inherited via extends, got %#v", links)
	}
	if len(files) != 3 {
		t.Fatalf("expected 3 contributing files, got %v", files)
	}
}

func TestResolveLayers_extendsCycleDetected(t *testing.T) {
	dir := t.TempDir()
	a := filepath.Join(dir, "a.yaml")
	b := filepath.Join(dir, "b.yaml")
	mustWrite(t, a, "extends: [b.yaml]\n")
	mustWrite(t, b, "extends: [a.yaml]\n")

	if _, _, err := ResolveLayers([]string{a}, nil); err == nil {
		t.Fatal("expected a cycle error")
	}
}

func TestResolveLayers_setsAppliedLast(t *testing.T) {
	dir := t.TempDir()
	f := filepath.Join(dir, "rig.yaml")
	mustWrite(t, f, "name: original\ndevices:\n  furnace:\n    config:\n      noise: 0.1\n")
	doc, _, err := ResolveLayers([]string{f}, []string{"devices.furnace.config.noise=0.5"})
	if err != nil {
		t.Fatal(err)
	}
	noise := doc["devices"].(map[string]any)["furnace"].(map[string]any)["config"].(map[string]any)["noise"]
	if noise != 0.5 {
		t.Fatalf("got %v", noise)
	}
}

func mustWrite(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}
