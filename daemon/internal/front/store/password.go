package store

import (
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"golang.org/x/crypto/scrypt"
)

// ErrBusy is Verify's answer when every hashing slot is taken: the front
// answers 429 with Retry-After rather than queueing the check.
var ErrBusy = errors.New("too many password checks at once")

// ErrNotScrypt wraps every reason a stored line is not a usable `$scrypt$`
// line. The front treats it as a misconfiguration (the D-028 fallback), and
// never compares a plaintext password.
var ErrNotScrypt = errors.New("not a $scrypt$ password line")

const scryptPrefix = "$scrypt$"

// Bounds on a line's parameters, so a config line cannot make one check
// allocate more than scryptMaxMemory (x/crypto's scrypt holds 128*r*n bytes).
// `flyball password` and the runner's hash_password write n=16384, r=8, p=1:
// 16 MiB.
const (
	scryptMaxMemory = 64 << 20
	scryptMaxP      = 16
	scryptMinHash   = 16
	scryptMaxHash   = 128
)

// scryptLine is a parsed `$scrypt$n=N,r=R,p=P$<salt>$<hash>` line: the format
// of cmd/flyball/local.go's hashPassword and the runner's auth.py
// hash_password (urlsafe base64 without padding; the hash is 64 bytes there,
// but its length is read from the line).
type scryptLine struct {
	n, r, p    int
	salt, hash []byte
}

func parseScrypt(line string) (scryptLine, error) {
	var s scryptLine
	rest, ok := strings.CutPrefix(line, scryptPrefix)
	if !ok {
		return s, fmt.Errorf("%w: no %s prefix", ErrNotScrypt, scryptPrefix)
	}
	parts := strings.Split(rest, "$")
	if len(parts) != 3 {
		return s, fmt.Errorf("%w: want params$salt$hash", ErrNotScrypt)
	}
	params := strings.Split(parts[0], ",")
	if len(params) != 3 {
		return s, fmt.Errorf("%w: parameters %q", ErrNotScrypt, parts[0])
	}
	for i, dst := range []*int{&s.n, &s.r, &s.p} {
		value, ok := strings.CutPrefix(params[i], "nrp"[i:i+1]+"=")
		v, err := strconv.Atoi(value)
		if !ok || err != nil {
			return s, fmt.Errorf("%w: parameters %q", ErrNotScrypt, parts[0])
		}
		*dst = v
	}
	if s.n < 2 || s.n&(s.n-1) != 0 || s.r < 1 || s.p < 1 || s.p > scryptMaxP ||
		int64(s.n)*int64(s.r) > scryptMaxMemory/128 {
		return s, fmt.Errorf("%w: parameters out of bounds (n=%d r=%d p=%d)", ErrNotScrypt, s.n, s.r, s.p)
	}
	var err error
	if s.salt, err = unb64(parts[1]); err != nil || len(s.salt) == 0 {
		return s, fmt.Errorf("%w: salt", ErrNotScrypt)
	}
	if s.hash, err = unb64(parts[2]); err != nil || len(s.hash) < scryptMinHash || len(s.hash) > scryptMaxHash {
		return s, fmt.Errorf("%w: hash", ErrNotScrypt)
	}
	return s, nil
}

// ParseScrypt reports whether line is a usable `$scrypt$` line, without
// hashing anything. The front calls it on its config's password: an error
// means the plaintext-password fallback, never a plaintext comparison.
func ParseScrypt(line string) error {
	_, err := parseScrypt(line)
	return err
}

// Hasher checks passwords against `$scrypt$` lines, at most a fixed number
// at once. It is safe for concurrent use; the front keeps one.
type Hasher struct {
	slots chan struct{}
	hash  func(plain, salt []byte, n, r, p, size int) ([]byte, error)
}

// NewHasher returns a Hasher with n slots (HashingSlots in the front).
func NewHasher(n int) *Hasher {
	if n < 1 {
		n = 1
	}
	return &Hasher{slots: make(chan struct{}, n), hash: scrypt.Key}
}

// Verify reports whether plain is the password stored holds. It returns
// ErrBusy at once, without hashing, when every slot is taken, and an error
// wrapping ErrNotScrypt when stored is not a usable line (a plaintext
// password included: it is never compared). The digests are compared in
// constant time.
func (h *Hasher) Verify(plain, stored string) (bool, error) {
	line, err := parseScrypt(stored)
	if err != nil {
		return false, err
	}
	select {
	case h.slots <- struct{}{}:
	default:
		return false, ErrBusy
	}
	defer func() { <-h.slots }()
	got, err := h.hash([]byte(plain), line.salt, line.n, line.r, line.p, len(line.hash))
	if err != nil {
		return false, err
	}
	return subtle.ConstantTimeCompare(got, line.hash) == 1, nil
}

func b64(raw []byte) string { return base64.RawURLEncoding.EncodeToString(raw) }

// unb64 accepts urlsafe base64 with or without padding, as auth.py writes
// none and a hand-edited line might carry some.
func unb64(text string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(strings.TrimRight(text, "="))
}
