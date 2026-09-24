// Package frontdir is the front's half of the front <-> runner channel's
// files: one directory per runner, mode 0700, owned by the front's euid,
// holding what the front writes before every spawn --
//
//	key       64 lower-case hex (32 bytes) and "\n", fresh at every spawn, 0600
//	aud       the audience the runner verifies principals against, and "\n", 0600
//	endpoint  the endpoint's string form ("unix:<dir>/sock" or "tcp:127.0.0.1:<port>") and "\n", 0600
//
// -- and what the runner makes there: `sock` (its unix socket) and
// `runner.lock` (flocked for its life, taken before it reads `key`; Write
// creates it and holds it while it writes). The runner is handed the
// directory in argv (`--front-dir DIR`); the key never travels in argv or
// the environment.
//
// The 0700 directory is the protection: uvicorn chmods its socket 0666.
package frontdir

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"flyballd/internal/endpoint"
)

// The files in a front-dir.
const (
	Key          = "key"
	Aud          = "aud"
	EndpointFile = "endpoint"
	Sock         = "sock"
	Lock         = "runner.lock"
)

// ErrLive: a runner holds the front-dir's runner.lock, so its key is not
// rewritten (and no second runner is spawned into it).
var ErrLive = errors.New("frontdir: a runner holds runner.lock")

// euid is swapped by a test to play another owner.
var euid = os.Geteuid

// FrontID names a front by its config: the first 8 hex digits of
// sha256(the absolute path). `flyball run` passes its first rig file.
func FrontID(path string) (string, error) {
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256([]byte(abs))
	return hex.EncodeToString(sum[:4]), nil
}

// Root is the parent directory for a front's front-dirs:
//
//   - under systemd with RuntimeDirectory=flyball, $RUNTIME_DIRECTORY
//     (/run/flyball), so a runner's dir is /run/flyball/<name>;
//   - otherwise $XDG_RUNTIME_DIR/flyball/<frontID>;
//   - "" when neither is set, or the one set is not a directory of the
//     front's own that nobody else can enter -- Dir then makes a temp dir.
//
// It creates nothing.
func Root(frontID string) string {
	if rd, _, _ := strings.Cut(os.Getenv("RUNTIME_DIRECTORY"), ":"); rd != "" && privateBase(rd) {
		return rd
	}
	if xdg := os.Getenv("XDG_RUNTIME_DIR"); xdg != "" && privateBase(xdg) {
		return filepath.Join(xdg, "flyball", frontID)
	}
	return ""
}

// privateBase: dir is an absolute directory of ours with no group or
// other permissions.
func privateBase(dir string) bool {
	if !filepath.IsAbs(dir) {
		return false
	}
	fi, err := os.Stat(dir)
	if err != nil || !fi.IsDir() || fi.Mode().Perm()&0o077 != 0 {
		return false
	}
	uid, ok := owner(fi)
	return !ok || uid == euid()
}

// Dir makes (or verifies) the front-dir for runner name under root and
// returns it. When root is "", or root/name/sock would not fit
// sun_path, it makes an unpredictable temp dir instead
// (os.MkdirTemp("", "flyball-<name>-")). Never a relative path.
func Dir(root, name string) (string, error) {
	if strings.ContainsAny(name, `/\`) || name == "" || name == "." || name == ".." {
		return "", fmt.Errorf("front-dir for %q: not a runner name", name)
	}
	if root != "" && filepath.IsAbs(root) && len(filepath.Join(root, name, Sock)) <= endpoint.MaxSocketPath {
		if err := mkdirs(root); err != nil {
			return "", err
		}
		dir := filepath.Join(root, name)
		if err := Prepare(dir); err != nil {
			return "", err
		}
		return dir, nil
	}
	dir, err := os.MkdirTemp("", "flyball-"+name+"-")
	if err != nil {
		return "", fmt.Errorf("front-dir for %s: %w", name, err)
	}
	if !filepath.IsAbs(dir) {
		os.Remove(dir)
		return "", fmt.Errorf("front-dir for %s: temp dir %q is not absolute", name, dir)
	}
	if len(filepath.Join(dir, Sock)) > endpoint.MaxSocketPath {
		os.Remove(dir)
		return "", fmt.Errorf("front-dir for %s: %s/%s is over %d bytes; set TMPDIR or XDG_RUNTIME_DIR to a shorter path", name, dir, Sock, endpoint.MaxSocketPath)
	}
	if err := Prepare(dir); err != nil {
		return "", err
	}
	return dir, nil
}

// mkdirs makes root's missing components 0700. Components that already
// exist are left as they are; the front-dir itself is what is verified.
func mkdirs(root string) error {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return fmt.Errorf("making %s: %w", root, err)
	}
	return nil
}

// Prepare makes dir 0700 if it does not exist, then Checks it. An
// existing dir that fails Check is refused, not repaired.
func Prepare(dir string) error {
	err := os.Mkdir(dir, 0o700)
	if err == nil {
		if err := os.Chmod(dir, 0o700); err != nil { // past the umask
			return fmt.Errorf("front-dir %s: %w", dir, err)
		}
	} else if !errors.Is(err, fs.ErrExist) {
		return fmt.Errorf("front-dir %s: %w", dir, err)
	}
	return Check(dir)
}

// Check verifies dir with lstat: a directory, not a symlink, owned by
// the front's euid, mode exactly 0700.
func Check(dir string) error {
	_, err := check(dir)
	return err
}

func check(dir string) (fs.FileInfo, error) {
	if !filepath.IsAbs(dir) {
		return nil, fmt.Errorf("front-dir %q: not an absolute path", dir)
	}
	fi, err := os.Lstat(dir)
	if err != nil {
		return nil, fmt.Errorf("front-dir %s: %w", dir, err)
	}
	switch {
	case fi.Mode()&fs.ModeSymlink != 0:
		return nil, fmt.Errorf("front-dir %s is a symlink", dir)
	case !fi.IsDir():
		return nil, fmt.Errorf("front-dir %s is not a directory", dir)
	}
	if uid, ok := owner(fi); ok && uid != euid() {
		return nil, fmt.Errorf("front-dir %s is owned by uid %d, not this front's %d", dir, uid, euid())
	}
	if m := fi.Mode().Perm(); m != 0o700 {
		return nil, fmt.Errorf("front-dir %s is mode %#o, not 0700", dir, m)
	}
	return fi, nil
}

// open Checks dir and opens it as an os.Root, confirming the root is the
// directory that was checked, so nothing written through it can land
// elsewhere.
func open(dir string) (*os.Root, error) {
	fi, err := check(dir)
	if err != nil {
		return nil, err
	}
	r, err := os.OpenRoot(dir)
	if err != nil {
		return nil, fmt.Errorf("front-dir %s: %w", dir, err)
	}
	opened, err := r.Stat(".")
	if err != nil || !os.SameFile(fi, opened) {
		r.Close()
		return nil, fmt.Errorf("front-dir %s changed while it was being opened", dir)
	}
	return r, nil
}

// Write gives the runner in dir a fresh key, and writes aud and ep
// beside it -- each file 0600, replaced atomically. It removes a stale
// `sock`. It refuses with ErrLive, touching nothing, while a runner holds
// runner.lock: a live runner's key is never rewritten. It holds
// runner.lock itself while it writes, so a runner starting meanwhile
// (which takes the lock before it reads the key) waits, then reads the
// new key -- never the old one with the new one written after it.
func Write(dir, aud string, ep endpoint.Endpoint) (key [32]byte, err error) {
	if aud == "" || strings.ContainsFunc(aud, func(r rune) bool { return r < 0x21 || r == 0x7f }) {
		return key, fmt.Errorf("front-dir %s: aud %q: empty, or a space or control character in it", dir, aud)
	}
	if err := ep.Validate(); err != nil {
		return key, err
	}
	r, err := open(dir)
	if err != nil {
		return key, err
	}
	defer r.Close()
	release, held, err := lockForWrite(r)
	if err != nil {
		return key, fmt.Errorf("front-dir %s: %w", dir, err)
	}
	if held {
		return key, fmt.Errorf("front-dir %s: %w", dir, ErrLive)
	}
	defer release()
	if _, err := rand.Read(key[:]); err != nil {
		return key, err
	}
	if err := r.Remove(Sock); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return key, fmt.Errorf("front-dir %s: removing a stale socket: %w", dir, err)
	}
	for _, f := range []struct{ name, content string }{
		{Aud, aud + "\n"},
		{EndpointFile, ep.String() + "\n"},
		{Key, hex.EncodeToString(key[:]) + "\n"},
	} {
		if err := writeFile(r, f.name, f.content); err != nil {
			return key, fmt.Errorf("front-dir %s: %w", dir, err)
		}
	}
	return key, nil
}

// writeFile replaces name in r with content, 0600, through a temp file
// and a rename, so a reader sees the old file or the new one.
func writeFile(r *os.Root, name, content string) error {
	tmp := name + ".tmp"
	f, err := r.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if err := f.Chmod(0o600); err != nil {
		f.Close()
		return err
	}
	if _, err := f.WriteString(content); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return r.Rename(tmp, name)
}

// LockHeld says whether a runner holds dir's runner.lock. It takes a
// shared lock for an instant to find out, so it is only asked when no
// runner of this front should be starting (before a spawn).
func LockHeld(dir string) (bool, error) {
	r, err := open(dir)
	if err != nil {
		return false, err
	}
	defer r.Close()
	return lockHeld(r)
}

// ReadKey reads dir's key: exactly 64 lower-case hex digits and an
// optional newline.
func ReadKey(dir string) (key [32]byte, err error) {
	r, err := open(dir)
	if err != nil {
		return key, err
	}
	defer r.Close()
	b, err := r.ReadFile(Key)
	if err != nil {
		return key, fmt.Errorf("front-dir %s: %w", dir, err)
	}
	s := strings.TrimSuffix(string(b), "\n")
	if len(s) != 64 || strings.ToLower(s) != s {
		return key, fmt.Errorf("front-dir %s: key is not 64 lower-case hex digits", dir)
	}
	if _, err := hex.Decode(key[:], []byte(s)); err != nil {
		return key, fmt.Errorf("front-dir %s: key: %w", dir, err)
	}
	return key, nil
}
