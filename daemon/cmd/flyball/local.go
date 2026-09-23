// Local commands: operations that never touch a runner or the daemon,
// mirrored from cli.py's cmd_password (engine/src/flyball/server/auth.py's
// hash_password) and cmd_new (engine/src/flyball/scaffold.py). Dispatched
// in main.go before target resolution, same as `run`/`daemon`/`logs`.
package main

import (
	"crypto/hmac"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"golang.org/x/crypto/scrypt"
	"golang.org/x/term"
)

// -- password --------------------------------------------------------------

// Matches auth.py's _N, _R, _P and the "$scrypt$n=...,r=...,p=...$salt$hash"
// format built by hash_password, base64 via _b64/_unb64 (urlsafe, no padding).
const (
	scryptN = 16384
	scryptR = 8
	scryptP = 1
	saltLen = 16
)

func runPasswordCommand(args []string) error {
	var plain string
	switch len(args) {
	case 0:
		fmt.Fprint(os.Stderr, "Password: ")
		data, err := term.ReadPassword(int(os.Stdin.Fd()))
		fmt.Fprintln(os.Stderr)
		if err != nil {
			return fmt.Errorf("reading password: %w", err)
		}
		plain = string(data)
	case 1:
		plain = args[0]
	default:
		return fmt.Errorf("usage: flyball password [PASSWORD]")
	}
	if plain == "" {
		return fmt.Errorf("an empty password is no password")
	}
	hash, err := hashPassword(plain)
	if err != nil {
		return err
	}
	fmt.Println(hash)
	return nil
}

// hashPassword mirrors auth.py's hash_password byte-for-byte: a random
// 16-byte salt, scrypt(N=16384, r=8, p=1) with hashlib.scrypt's default
// dklen (64 bytes -- Python's stdlib default, not scrypt's usual 32),
// urlsafe-base64 with padding stripped.
func hashPassword(plain string) (string, error) {
	salt := make([]byte, saltLen)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	digest, err := scrypt.Key([]byte(plain), salt, scryptN, scryptR, scryptP, 64)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("$scrypt$n=%d,r=%d,p=%d$%s$%s", scryptN, scryptR, scryptP, b64(salt), b64(digest)), nil
}

// verifyPassword mirrors auth.py's verify_password for the scrypt branch
// (the plaintext htpasswd-style fallback isn't needed here; this command
// only ever hashes/verifies scrypt lines it or Python produced).
func verifyPassword(plain, stored string) bool {
	const prefix = "$scrypt$"
	if !strings.HasPrefix(stored, prefix) {
		return hmac.Equal([]byte(plain), []byte(stored))
	}
	parts := strings.Split(stored[len(prefix):], "$")
	if len(parts) != 3 {
		return false
	}
	var n, r, p int
	if _, err := fmt.Sscanf(parts[0], "n=%d,r=%d,p=%d", &n, &r, &p); err != nil {
		return false
	}
	salt, err := unb64(parts[1])
	if err != nil {
		return false
	}
	expected, err := unb64(parts[2])
	if err != nil {
		return false
	}
	got, err := scrypt.Key([]byte(plain), salt, n, r, p, len(expected))
	if err != nil {
		return false
	}
	return hmac.Equal(got, expected)
}

func b64(raw []byte) string {
	return strings.TrimRight(base64.URLEncoding.EncodeToString(raw), "=")
}

func unb64(text string) ([]byte, error) {
	if pad := len(text) % 4; pad != 0 {
		text += strings.Repeat("=", 4-pad)
	}
	return base64.URLEncoding.DecodeString(text)
}

// -- new ---------------------------------------------------------------

// pythonKeywords mirrors keyword.iskeyword's kwlist (Python 3.12), since
// Go has no equivalent to call. soft keywords (match/case/type/_) are
// deliberately excluded here as cli.py's scaffold.py checks keyword.iskeyword,
// not keyword.issoftkeyword.
var pythonKeywords = map[string]bool{
	"False": true, "None": true, "True": true, "and": true, "as": true,
	"assert": true, "async": true, "await": true, "break": true, "class": true,
	"continue": true, "def": true, "del": true, "elif": true, "else": true,
	"except": true, "finally": true, "for": true, "from": true, "global": true,
	"if": true, "import": true, "in": true, "is": true, "lambda": true,
	"nonlocal": true, "not": true, "or": true, "pass": true, "raise": true,
	"return": true, "try": true, "while": true, "with": true, "yield": true,
}

var nonIdentRun = regexp.MustCompile(`[^0-9a-zA-Z]+`)

// identifier mirrors scaffold.py's _identifier: collapse non-alphanumeric
// runs to '_', strip leading/trailing '_', lowercase, then refuse if empty,
// digit-leading, or a Python keyword.
func identifier(name string) (string, error) {
	snake := strings.ToLower(strings.Trim(nonIdentRun.ReplaceAllString(name, "_"), "_"))
	if snake == "" || (snake[0] >= '0' && snake[0] <= '9') || pythonKeywords[snake] {
		return "", fmt.Errorf("%q does not make a Python identifier", name)
	}
	return snake, nil
}

// deviceTemplate mirrors scaffold.py's _DEVICE Template exactly (same
// substitution points: Title, type, name).
const deviceTemplate = `"""%[1]s: a device driver built for this rig.

Registered as ` + "`driver: \"%[2]s\"`" + `, so a rig file can declare one:

    devices:
      %[3]s:
        driver: %[2]s
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.core.device import DriverConfig, Output, Readable, command
from flyball.core.quantity import Quantity
from flyball.core.signal import Node, Sample
from flyball.core.units import Unit


class %[1]s(Readable):
    """TODO: what this measures or drives, and how."""

    value = Output("value", "Value", Quantity("value", Unit.get("1")))

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """Called every ` + "`poll_s`" + `; yield the signals due at ` + "`time_ns`" + `."""
        yield self.sample(time_ns, value=0.0)  # TODO: read the hardware

    @command
    def reset(self) -> None:
        """TODO: something an operator or program can trigger."""
        self.value.push(0.0)


class %[1]sConfig(DriverConfig[%[1]s], type="%[2]s"):
    """The rig-file entry: ` + "`driver: %[2]s`" + `, its fields flat beside it."""

    def build(self, name: str, label: str | None = None) -> %[1]s:
        return %[1]s(name, label)
`

// renderDevice mirrors scaffold.py's render: the type is the snake_case
// name, the class name its CamelCase form.
func renderDevice(name string) (string, error) {
	snake, err := identifier(name)
	if err != nil {
		return "", err
	}
	parts := strings.Split(snake, "_")
	title := ""
	for _, part := range parts {
		if part == "" {
			continue
		}
		title += strings.ToUpper(part[:1]) + part[1:]
	}
	return fmt.Sprintf(deviceTemplate, title, snake, snake), nil
}

func runNewCommand(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: flyball new NAME [--dir PATH]")
	}
	name := args[0]
	dir := "."
	rest := args[1:]
	for i := 0; i < len(rest); i++ {
		if rest[i] == "--dir" {
			if i+1 >= len(rest) {
				return fmt.Errorf("--dir needs a path")
			}
			dir = rest[i+1]
			i++
			continue
		}
		return fmt.Errorf("unknown argument %q", rest[i])
	}

	snake, err := identifier(name)
	if err != nil {
		return err
	}
	path := filepath.Join(dir, snake+".py")
	if _, err := os.Stat(path); err == nil {
		return fmt.Errorf("%s exists; not overwriting", path)
	}
	content, err := renderDevice(name)
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return err
	}
	fmt.Printf("wrote %s; add it to the rig's package and declare driver = %q in the file\n", path, snake)
	return nil
}
