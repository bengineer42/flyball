package main

import (
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// asRoot makes the token commands believe they run as root (euid 0).
func asRoot(t *testing.T) {
	was := tokenEuid
	tokenEuid = func() int { return 0 }
	t.Cleanup(func() { tokenEuid = was })
}

func myName(t *testing.T) string {
	if u, err := user.LookupId(strconv.Itoa(os.Getuid())); err == nil {
		return u.Username
	}
	return strconv.Itoa(os.Getuid())
}

// TestTokenAsRootRefusesAnotherUsersFront: `sudo flyball token create`
// against a front whose state dir belongs to another user (here: the test's
// own user, root played by an injected euid) would leave tokens.json and
// audit.jsonl root's, and that front would refuse sign-ins. It is refused,
// naming `sudo -u <owner>`, and nothing is written.
func TestTokenAsRootRefusesAnotherUsersFront(t *testing.T) {
	for name, existing := range map[string]bool{"the state dir exists": true, "only an ancestor exists": false} {
		t.Run(name, func(t *testing.T) {
			state := t.TempDir()
			t.Setenv("XDG_STATE_HOME", state)
			rig := filepath.Join(t.TempDir(), "rig.yaml")
			os.WriteFile(rig, []byte("devices: {}\n"), 0o644)
			path, err := tokensPathFor(rig, false)
			if err != nil {
				t.Fatal(err)
			}
			if existing {
				os.MkdirAll(filepath.Dir(path), 0o700)
			}
			asRoot(t)
			for cmd, run := range map[string]func() error{
				"create": func() error { return runTokenCreate([]string{"--name", "x", "--config", rig}) },
				"list":   func() error { return runTokenList([]string{"--config", rig}) },
				"revoke": func() error { return runTokenRevoke([]string{"fbt1_nope", "--config", rig}) },
			} {
				var err error
				captureStdout(t, func() { err = run() })
				if err == nil || !strings.Contains(err.Error(), "sudo -u "+myName(t)) {
					t.Errorf("token %s as root: err = %v, want a refusal naming `sudo -u %s`", cmd, err, myName(t))
				}
			}
			for _, f := range []string{path, filepath.Join(filepath.Dir(path), "audit.jsonl"), path + ".lock"} {
				if _, err := os.Stat(f); err == nil {
					t.Errorf("%s was created", f)
				}
			}
			if !existing {
				if _, err := os.Stat(filepath.Dir(path)); err == nil {
					t.Errorf("the state dir %s was created", filepath.Dir(path))
				}
			}
		})
	}
}

// TestTokenNotRootIsUnchanged: as an ordinary user nothing changes.
func TestTokenNotRootIsUnchanged(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	rig := filepath.Join(t.TempDir(), "rig.yaml")
	os.WriteFile(rig, []byte("devices: {}\n"), 0o644)
	captureStdout(t, func() {
		if err := runTokenCreate([]string{"--name", "x", "--config", rig}); err != nil {
			t.Fatal(err)
		}
	})
}
