package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"flyballd/internal/userconfig"
)

// initEnv points the config file, the data dir and HOME into a temp dir,
// and init's output into a buffer.
func initEnv(t *testing.T) (dir, config string, out *bytes.Buffer) {
	t.Helper()
	dir = t.TempDir()
	config = filepath.Join(dir, "config", "flyball", "config.yaml")
	t.Setenv("FLYBALL_CONFIG", config)
	t.Setenv("XDG_DATA_HOME", filepath.Join(dir, "data"))
	t.Setenv("HOME", filepath.Join(dir, "home"))
	out = new(bytes.Buffer)
	old := initOut
	initOut = out
	t.Cleanup(func() { initOut = old })
	return dir, config, out
}

func TestInitMakesTheFoldersAndTheFile(t *testing.T) {
	dir, config, out := initEnv(t)
	if err := runInitCommand(nil); err != nil {
		t.Fatal(err)
	}
	text, err := os.ReadFile(config)
	if err != nil {
		t.Fatal(err)
	}
	template, _ := userconfig.Template()
	if string(text) != template {
		t.Fatalf("the file is not the template:\n%s", text)
	}
	data := filepath.Join(dir, "data", "flyball")
	for _, name := range userconfig.Folders {
		if fi, err := os.Stat(filepath.Join(data, name)); err != nil || !fi.IsDir() {
			t.Errorf("%s: %v", name, err)
		}
	}
	for _, want := range []string{"wrote  " + config, "made   " + filepath.Join(data, "devices") + "/", "No Python environment", "python3 -m venv " + filepath.Join(data, "envs", "default", ".venv")} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("output lacks %q:\n%s", want, out)
		}
	}

	// Again: the file (edited since) is kept, the folders exist; a venv
	// with flyball-runner in it is named as the one flyball run uses.
	edited := "# mine\n"
	os.WriteFile(config, []byte(edited), 0o644)
	runner := userconfig.Runner(userconfig.DefaultVenv(data))
	os.MkdirAll(filepath.Dir(runner), 0o755)
	os.WriteFile(runner, nil, 0o755)
	out.Reset()
	if err := runInitCommand(nil); err != nil {
		t.Fatal(err)
	}
	if text, _ := os.ReadFile(config); string(text) != edited {
		t.Fatalf("init overwrote the file: %q", text)
	}
	for _, want := range []string{"kept   " + config, "exists " + filepath.Join(data, "views") + "/", "flyball run uses " + runner} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("second run's output lacks %q:\n%s", want, out)
		}
	}
	if strings.Contains(out.String(), "made") {
		t.Errorf("second run made something:\n%s", out)
	}
}

// data_dir in an existing file is where init makes the folders.
func TestInitFollowsDataDir(t *testing.T) {
	dir, config, _ := initEnv(t)
	elsewhere := filepath.Join(dir, "elsewhere")
	os.MkdirAll(filepath.Dir(config), 0o700)
	os.WriteFile(config, []byte("data_dir: "+elsewhere+"\n"), 0o644)
	if err := runInitCommand(nil); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(elsewhere, "blocks")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, "data")); !os.IsNotExist(err) {
		t.Fatalf("the default data dir was made too: %v", err)
	}
}

func TestInitPrintWritesNothing(t *testing.T) {
	dir, config, out := initEnv(t)
	if err := runInitCommand([]string{"--print"}); err != nil {
		t.Fatal(err)
	}
	template, _ := userconfig.Template()
	if out.String() != template {
		t.Fatalf("--print:\n%s", out)
	}
	if !strings.Contains(template, "#data_dir: "+filepath.Join(dir, "data", "flyball")+"\n") {
		t.Fatalf("the template's data_dir is not this machine's default:\n%s", template)
	}
	if _, err := os.Stat(config); !os.IsNotExist(err) {
		t.Fatalf("--print wrote the file: %v", err)
	}
	if err := runInitCommand([]string{"--force"}); err == nil || err.Error() != initUsage {
		t.Fatalf("an unknown flag: %v", err)
	}
}

// flyball run's runner: the config's venv's flyball-runner when it has
// one; the default venv not made, PATH's; a named venv without one, an error.
func TestLocalRunner(t *testing.T) {
	dir, config, _ := initEnv(t)
	if got, err := localRunner(); err != nil || got != runnerCommand {
		t.Fatalf("no venv: %q, %v", got, err)
	}
	venv := filepath.Join(dir, "venv")
	os.MkdirAll(filepath.Dir(config), 0o700)
	os.WriteFile(config, []byte("venv: "+venv+"\n"), 0o644)
	if _, err := localRunner(); err == nil || !strings.Contains(err.Error(), "has no flyball-runner") {
		t.Fatalf("a named venv without the runner: %v", err)
	}
	runner := userconfig.Runner(venv)
	os.MkdirAll(filepath.Dir(runner), 0o755)
	os.WriteFile(runner, nil, 0o755)
	if got, err := localRunner(); err != nil || got != runner {
		t.Fatalf("the named venv: %q, %v", got, err)
	}
	os.WriteFile(config, []byte("venv: [\n"), 0o644)
	if _, err := localRunner(); err == nil {
		t.Fatal("a file that cannot be read did not stop the run")
	}
}

// flyball run starts the flyball-runner in the config's venv, not PATH's,
// and says which.
func TestRunUsesTheConfigsVenv(t *testing.T) {
	dir := fakeEnv(t)
	self := runnerCommand // the fake runner
	runnerCommand = filepath.Join(dir, "not-on-path", "flyball-runner")
	venv := filepath.Join(dir, "venv")
	runner := userconfig.Runner(venv)
	os.MkdirAll(filepath.Dir(runner), 0o755)
	if err := os.Symlink(self, runner); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(dir, "config.yaml"), []byte("venv: "+venv+"\n"), 0o644)
	rig := filepath.Join(dir, "rig.yaml")
	os.WriteFile(rig, []byte("name: t\n"), 0o644)
	r := &fakeRun{t: t, dir: dir, marker: filepath.Join(dir, "stopped"), args: filepath.Join(dir, "args"),
		sigs: make(chan os.Signal, 2), done: make(chan error, 1), addrs: make(chan string, 4)}
	runOut = r
	r.listen()
	go func() { r.done <- run([]string{rig, "--listen", "127.0.0.1:0"}, r.sigs) }()
	t.Cleanup(func() { r.stop() })
	echoAt(t, r, r.addr(), nil)
	if !strings.Contains(r.output(), "flyball: the runner is "+runner) {
		t.Fatalf("the banner does not name the venv's runner:\n%s", r.output())
	}
}

// init never overwrites or removes: a config file of the user's own, files
// in the folders, and a file where a folder would go all survive it.
func TestInitIsNotDestructive(t *testing.T) {
	dir, config, _ := initEnv(t)
	data := filepath.Join(dir, "data", "flyball")
	mine := map[string]string{
		config: "venv: /opt/my-venv\n",
		filepath.Join(data, "devices", "sht4x.py"):   "# a driver\n",
		filepath.Join(data, "rigs", "a", "rig.yaml"): "name: a\n",
		filepath.Join(data, "notes.txt"):             "mine\n",
	}
	for path, text := range mine {
		os.MkdirAll(filepath.Dir(path), 0o700)
		os.WriteFile(path, []byte(text), 0o600)
	}
	if err := runInitCommand(nil); err != nil {
		t.Fatal(err)
	}
	// A file where the views folder goes: init stops, and leaves it.
	views := filepath.Join(data, "views")
	os.Remove(views)
	os.WriteFile(views, []byte("not a folder\n"), 0o600)
	if err := runInitCommand(nil); err == nil {
		t.Fatal("init made a folder over a file, or said nothing")
	}
	mine[views] = "not a folder\n"
	for path, text := range mine {
		got, err := os.ReadFile(path)
		if err != nil || string(got) != text {
			t.Errorf("%s: %q, %v; want %q untouched", path, got, err, text)
		}
		if fi, _ := os.Stat(path); fi.Mode().Perm() != 0o600 {
			t.Errorf("%s: mode %v, want 0600 untouched", path, fi.Mode().Perm())
		}
	}
}

// flyball run's leading arguments: a name is the rig in rigs/<name>/
// (either spelling), a path is a file, -p/--path is a file whatever it
// looks like, and the rest is left for the front and the runner.
func TestResolveRigArgs(t *testing.T) {
	dir, _, _ := initEnv(t)
	rigs := filepath.Join(dir, "data", "flyball", "rigs")
	chamber := filepath.Join(rigs, "chamber_a", "rig.yaml")
	os.MkdirAll(filepath.Dir(chamber), 0o755)
	os.WriteFile(chamber, []byte("name: chamber_a\n"), 0o644)
	os.MkdirAll(filepath.Join(rigs, "empty"), 0o755)
	t.Chdir(dir)
	os.WriteFile("oven", []byte("name: oven\n"), 0o644)
	os.WriteFile("chamber-a", []byte("name: file\n"), 0o644)

	for _, c := range []struct {
		args []string
		want []string
	}{
		{[]string{"chamber_a"}, []string{chamber}},
		{[]string{"chamber-a", "sim.yaml", "--set", "x=1", "oven"}, []string{chamber, "sim.yaml", "--set", "x=1", "oven"}},
		{[]string{"-p", "oven", "--path", "chamber-a", "--path=./x", "--listen", "a:1", "-p", "y"}, []string{"oven", "chamber-a", "./x", "--listen", "a:1", "-p", "y"}},
		{[]string{"./oven", "/etc/rig.yaml", "Oven"}, []string{"./oven", "/etc/rig.yaml", "Oven"}},
	} {
		got, err := resolveRigArgs(c.args)
		if err != nil || strings.Join(got, " ") != strings.Join(c.want, " ") {
			t.Errorf("%q: got %q, %v; want %q", c.args, got, err, c.want)
		}
	}
	for args, want := range map[string]string{
		"oven":  "no rig called oven in " + rigs + "; for the file oven here: flyball run -p oven",
		"empty": "rig empty: " + filepath.Join(rigs, "empty") + " has no rig.yaml",
		"-p":    "-p needs a rig file",
	} {
		if _, err := resolveRigArgs([]string{args}); err == nil || !strings.HasPrefix(err.Error(), want) {
			t.Errorf("%s: %v, want %q", args, err, want)
		}
	}
	os.MkdirAll(filepath.Join(rigs, "chamber-a"), 0o755)
	if _, err := resolveRigArgs([]string{"chamber_a"}); err == nil || !strings.Contains(err.Error(), "keep one") {
		t.Errorf("both spellings: %v", err)
	}
}
