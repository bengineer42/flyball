// Package userconfig is the flyball binary's own configuration, one file
// per user: where flyball keeps its library and environments, and which
// Python environment `flyball run` starts flyball-runner from. It is not a
// rig file and never reaches the runner.
//
// The file is `$FLYBALL_CONFIG`, else `<os.UserConfigDir>/flyball/config.yaml`
// (`~/.config/flyball/config.yaml` on Linux), beside the tokens `flyball
// login` saves. `flyball init` writes it with every key commented out at its
// default, as sshd_config is shipped, and makes the folders.
package userconfig

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"gopkg.in/yaml.v3"
)

// Folders are the ones `flyball init` makes under the data dir: the
// environments, one folder per rig on this machine, and one per library
// type (tasks/layout-and-names B14).
var Folders = []string{
	"envs", "rigs",
	"devices", "links", "blocks", "trajectories", "actions", "conditions", "views", "quantities",
}

// Config is the file as resolved: every path absolute, a default filled in
// where the file says nothing.
type Config struct {
	Path    string // the file read (it may not exist)
	Exists  bool   // whether it did
	DataDir string
	Venv    string
	// VenvSet is whether the file named the venv: a named venv without
	// flyball-runner in it is an error, the default one is only skipped.
	VenvSet bool
}

// file is what may be written in it; a key not here is refused.
type file struct {
	DataDir *string `yaml:"data_dir"`
	Venv    *string `yaml:"venv"`
}

// Path is the config file: $FLYBALL_CONFIG, else config.yaml in flyball's
// folder under os.UserConfigDir.
func Path() (string, error) {
	if p := os.Getenv("FLYBALL_CONFIG"); p != "" {
		return filepath.Abs(p)
	}
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("finding a config directory: %w", err)
	}
	return filepath.Join(dir, "flyball", "config.yaml"), nil
}

// DefaultDataDir is where the library and the environments go when the
// file does not say: $XDG_DATA_HOME/flyball, else ~/.local/share/flyball;
// on macOS ~/Library/Application Support/flyball, on Windows
// %LOCALAPPDATA%\flyball.
func DefaultDataDir() (string, error) {
	switch runtime.GOOS {
	case "windows":
		if d := os.Getenv("LOCALAPPDATA"); d != "" {
			return filepath.Join(d, "flyball"), nil
		}
		return "", errors.New("no data directory: %LOCALAPPDATA% is not set")
	case "darwin":
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("no data directory: %w", err)
		}
		return filepath.Join(home, "Library", "Application Support", "flyball"), nil
	}
	if d := os.Getenv("XDG_DATA_HOME"); d != "" && filepath.IsAbs(d) {
		return filepath.Join(d, "flyball"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("no data directory: set HOME, or XDG_DATA_HOME to an absolute path: %w", err)
	}
	return filepath.Join(home, ".local", "share", "flyball"), nil
}

// DefaultVenv is the environment under a data dir that `flyball run` uses
// when the file names none.
func DefaultVenv(dataDir string) string {
	return filepath.Join(dataDir, "envs", "default", ".venv")
}

// Runner is flyball-runner inside a venv.
func Runner(venv string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(venv, "Scripts", "flyball-runner.exe")
	}
	return filepath.Join(venv, "bin", "flyball-runner")
}

// Load reads the config file, if there is one, and fills in the defaults.
// A missing file is the defaults; a file that cannot be parsed, or names a
// key it does not know, or gives a relative path, is an error naming it.
func Load() (Config, error) {
	path, err := Path()
	if err != nil {
		return Config{}, err
	}
	c := Config{Path: path}
	var f file
	data, err := os.ReadFile(path)
	switch {
	case errors.Is(err, fs.ErrNotExist):
	case err != nil:
		return c, err
	default:
		c.Exists = true
		dec := yaml.NewDecoder(bytes.NewReader(data))
		dec.KnownFields(true)
		// io.EOF is a file of comments only, as flyball init writes it: the defaults.
		if err := dec.Decode(&f); err != nil && !errors.Is(err, io.EOF) {
			return c, fmt.Errorf("%s: %w", path, err)
		}
	}
	if f.DataDir != nil {
		if c.DataDir, err = expand(*f.DataDir); err != nil {
			return c, fmt.Errorf("%s: data_dir: %w", path, err)
		}
	} else if c.DataDir, err = DefaultDataDir(); err != nil {
		return c, err
	}
	if f.Venv != nil {
		if c.Venv, err = expand(*f.Venv); err != nil {
			return c, fmt.Errorf("%s: venv: %w", path, err)
		}
		c.VenvSet = true
	} else {
		c.Venv = DefaultVenv(c.DataDir)
	}
	return c, nil
}

// expand makes a path from the file absolute: `~` or `~/…` is the home
// directory; anything else must already be absolute, since the file is
// read from wherever flyball is run.
func expand(p string) (string, error) {
	if p == "~" || strings.HasPrefix(p, "~/") || strings.HasPrefix(p, `~\`) {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		return filepath.Join(home, p[1:]), nil
	}
	if !filepath.IsAbs(p) {
		return "", fmt.Errorf("%q is relative: give an absolute path, or one starting ~/", p)
	}
	return filepath.Clean(p), nil
}

// Tilde is p with the home directory written as ~, for what flyball prints
// and the file it writes.
func Tilde(p string) string {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return p
	}
	if p == home {
		return "~"
	}
	if rest, ok := strings.CutPrefix(p, home+string(filepath.Separator)); ok {
		return "~/" + filepath.ToSlash(rest)
	}
	return p
}

// Template is the file `flyball init` writes: every key commented out at
// its default for this machine, so the file as written changes nothing.
func Template() (string, error) {
	data, err := DefaultDataDir()
	if err != nil {
		return "", err
	}
	return fmt.Sprintf(`# flyball's own settings, for this user. Not a rig file: nothing here
# reaches a rig. FLYBALL_CONFIG names another file instead of this one.
#
# Every key is shown at its default, commented out: uncomment a line to
# change it. Paths are absolute, or start with ~/.
# `+"`flyball init --print`"+` prints this file as this flyball knows it.

# Where flyball keeps its environments (envs/), one folder per rig on this
# machine (rigs/: `+"`flyball run NAME`"+` starts rigs/NAME/rig.yaml), and its
# library, a folder per type: devices, links, blocks, trajectories,
# actions, conditions, views, quantities.
# `+"`flyball init`"+` makes them; run it again after changing this.
#data_dir: %s

# The Python environment `+"`flyball run`"+` starts flyball-runner from: a venv
# with flyball installed in it. Default: envs/default/.venv in data_dir.
# Named here, it must have flyball-runner in it. Left at the default and
# not made, flyball-runner is looked for on PATH instead. `+"`flyball run --uv`"+`
# (or runner.front.uv in the rig) uses uv, and ignores this.
#venv: %s
`, Tilde(data), Tilde(DefaultVenv(data))), nil
}
