//go:build unix

package store

import (
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

// The mode is the code's doing, not the umask's: with umask 0 a plain
// create would make the file 0666 and the dir 0777.
func TestTokensModeIgnoresUmask(t *testing.T) {
	old := syscall.Umask(0)
	defer syscall.Umask(old)
	path := filepath.Join(t.TempDir(), "front", "tokens.json")
	tok, err := OpenTokens(path, TokensOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := tok.Create(NewToken{Name: "ci"}); err != nil {
		t.Fatal(err)
	}
	for p, want := range map[string]os.FileMode{
		path:               0o600,
		path + ".lock":     0o600,
		filepath.Dir(path): 0o700 | os.ModeDir,
	} {
		info, err := os.Stat(p)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode() != want {
			t.Errorf("%s: mode %v under umask 0; want %v", filepath.Base(p), info.Mode(), want)
		}
	}
}

// A file left group- or world-readable (by hand, or an older tool) is
// brought back to 0600 at the next write.
func TestTokensModeRepaired(t *testing.T) {
	f := newTokenFixture(t)
	f.create(t, NewToken{})
	if err := os.Chmod(f.path, 0o644); err != nil {
		t.Fatal(err)
	}
	f.create(t, NewToken{Name: "second"})
	info, _ := os.Stat(f.path)
	if info.Mode() != 0o600 {
		t.Errorf("mode %v after a write; want 0600", info.Mode())
	}
}
