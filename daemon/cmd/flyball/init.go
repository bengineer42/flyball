// `flyball init`: the folders flyball keeps things in and the config file
// that says where they are (internal/userconfig), for a machine that has
// only the binary. Local, dispatched in main.go before target resolution.
package main

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"

	"flyballd/internal/userconfig"
)

const initUsage = "usage: flyball init [--print]"

// initOut is where init writes; a variable for the tests.
var initOut io.Writer = os.Stdout

func runInitCommand(args []string) error {
	switch {
	case len(args) == 1 && args[0] == "--print":
		text, err := userconfig.Template()
		if err != nil {
			return err
		}
		fmt.Fprint(initOut, text)
		return nil
	case len(args) != 0:
		return errors.New(initUsage)
	}

	// An existing file says where the data dir is, so a second init after
	// changing data_dir makes the folders there.
	cfg, err := userconfig.Load()
	if err != nil {
		return err
	}
	if cfg.Exists {
		fmt.Fprintf(initOut, "kept   %s\n", userconfig.Tilde(cfg.Path))
	} else {
		text, err := userconfig.Template()
		if err != nil {
			return err
		}
		if err := os.MkdirAll(filepath.Dir(cfg.Path), 0o700); err != nil {
			return err
		}
		// O_EXCL: a file that appeared since Load is kept, never overwritten.
		f, err := os.OpenFile(cfg.Path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err != nil {
			return err
		}
		_, werr := f.WriteString(text)
		if cerr := f.Close(); werr == nil {
			werr = cerr
		}
		if werr != nil {
			return werr
		}
		fmt.Fprintf(initOut, "wrote  %s\n", userconfig.Tilde(cfg.Path))
	}

	for _, name := range userconfig.Folders {
		dir := filepath.Join(cfg.DataDir, name)
		verb := "made  "
		if _, err := os.Stat(dir); err == nil {
			verb = "exists"
		} else if !errors.Is(err, fs.ErrNotExist) {
			return err
		}
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
		fmt.Fprintf(initOut, "%s %s%c\n", verb, userconfig.Tilde(dir), filepath.Separator)
	}

	runner := userconfig.Runner(cfg.Venv)
	if _, err := os.Stat(runner); err == nil {
		fmt.Fprintf(initOut, "\nflyball run uses %s\n", userconfig.Tilde(runner))
		return nil
	}
	venv, bin := userconfig.Tilde(cfg.Venv), userconfig.Tilde(filepath.Dir(runner))
	fmt.Fprintf(initOut, `
No Python environment at %s yet. Make one and install flyball into it,
from a checkout of flyball:
  python3 -m venv %s
  %s/pip install -e <checkout>/sim -e '<checkout>/engine[server]'
flyball run then starts %s.
`, venv, venv, bin, userconfig.Tilde(runner))
	return nil
}
