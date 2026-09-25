package userconfig

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// isolate points the config file and the data dir into a temp dir.
func isolate(t *testing.T) (dir, path string) {
	t.Helper()
	dir = t.TempDir()
	path = filepath.Join(dir, "config", "config.yaml")
	t.Setenv("FLYBALL_CONFIG", path)
	t.Setenv("XDG_DATA_HOME", filepath.Join(dir, "data"))
	t.Setenv("HOME", filepath.Join(dir, "home"))
	return dir, path
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestNoFileIsTheDefaults(t *testing.T) {
	dir, path := isolate(t)
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	data := filepath.Join(dir, "data", "flyball")
	if c.Path != path || c.Exists || c.DataDir != data || c.Venv != filepath.Join(data, "envs", "default", ".venv") || c.VenvSet {
		t.Fatalf("got %+v", c)
	}
}

// The file init writes changes nothing as written, and each commented-out
// line, uncommented, is a key Load knows at the value it defaults to.
func TestTheTemplateIsTheDefaults(t *testing.T) {
	_, path := isolate(t)
	want, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	text, err := Template()
	if err != nil {
		t.Fatal(err)
	}
	write(t, path, text)
	got, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if !got.Exists || got.DataDir != want.DataDir || got.Venv != want.Venv || got.VenvSet {
		t.Fatalf("as written: got %+v, want the defaults %+v", got, want)
	}

	key := regexp.MustCompile(`(?m)^#([a-z_]+: )`)
	if n := len(key.FindAllString(text, -1)); n != 2 {
		t.Fatalf("%d commented-out keys in the template, want 2 (data_dir, venv)", n)
	}
	write(t, path, key.ReplaceAllString(text, "$1"))
	got, err = Load()
	if err != nil {
		t.Fatal(err)
	}
	if got.DataDir != want.DataDir || got.Venv != want.Venv || !got.VenvSet {
		t.Fatalf("uncommented: got %+v, want %+v with the venv set", got, want)
	}
}

func TestPathsAndMistakes(t *testing.T) {
	dir, path := isolate(t)
	home := filepath.Join(dir, "home")

	write(t, path, "data_dir: ~/lab\n")
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if c.DataDir != filepath.Join(home, "lab") || c.Venv != filepath.Join(home, "lab", "envs", "default", ".venv") {
		t.Fatalf("~ and the venv following data_dir: got %+v", c)
	}
	if Tilde(c.Venv) != "~/lab/envs/default/.venv" {
		t.Fatalf("Tilde: %q", Tilde(c.Venv))
	}

	for text, want := range map[string]string{
		"venv: envs/x\n":       "relative",
		"data_dir: lab\n":      "relative",
		"datadir: /x\n":        "field datadir not found",
		"venv: [1, 2]\n":       "cannot unmarshal",
		"data_dir: /a\n  b: 1": "yaml",
	} {
		write(t, path, text)
		_, err := Load()
		if err == nil || !strings.Contains(err.Error(), want) || !strings.Contains(err.Error(), path) {
			t.Errorf("%q: got %v, want an error naming the file and %q", text, err, want)
		}
	}
}
