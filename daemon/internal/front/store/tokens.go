package store

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"
)

// TokenPrefix starts every named token: `fbt1_` + 43 base64url characters
// (32 random bytes). The prefix lets the front tell a named token from any
// other bearer, and a secret scanner find one in a leak.
const TokenPrefix = "fbt1_"

// Token kinds (the Identity.Kind vocabulary of §WP0-6). A token created
// without one is KindService.
const (
	KindHuman   = "human"
	KindService = "service"
	KindAgent   = "agent"
)

// tokenNameMax bounds a token's name, which reaches logs and the audit.
const tokenNameMax = 64

var (
	// ErrNoToken: the secret is not a token this store holds (a malformed
	// one, an unknown one, a revoked one). The front answers 401.
	ErrNoToken = errors.New("no such token")
	// ErrTokenExpired: the token is held but past its expiry. The front
	// answers 401.
	ErrTokenExpired = errors.New("token expired")
	// ErrTokenNotFound: Revoke was given an id the file does not hold.
	ErrTokenNotFound = errors.New("no token with that id")
)

// Any other error from Lookup, List, Create, Revoke or Sweep is the store's
// own failure (an unreadable or malformed file): the front answers 503 and
// never falls through to anonymous (F10).

// Token is a named token as the front shows it; its JSON is the §WP0-8 row
// of `GET /api/auth/tokens`. The secret is never part of it.
type Token struct {
	ID       string     `json:"id"`
	Name     string     `json:"name"`
	Scopes   []string   `json:"scopes"` // the ceiling, pinned at issue; opaque strings (D-034 pending)
	Kind     string     `json:"kind"`
	Created  time.Time  `json:"created"`
	Expires  time.Time  `json:"expires"`
	LastUsed *time.Time `json:"last_used"`
	// Issuer is who created the token (the creating identity's sub, or ""
	// for the CLI). The front intersects Scopes with the issuer's current
	// verbs at every mint (merge requirement 12); the store only keeps it.
	Issuer string `json:"-"`
	// Cleartext records that the token was created over a connection
	// without TLS, which capped its lifetime.
	Cleartext bool `json:"-"`
}

// NewToken is what Create is asked for.
type NewToken struct {
	Name   string
	Scopes []string // stored as given; nil is stored as []
	Kind   string   // "" is KindService
	Issuer string
	// ExpiresIn is the requested lifetime: 0 asks for the default, and more
	// than the cap gets the cap (TokenLifetime). Negative is an error.
	ExpiresIn time.Duration
	// Cleartext: the request came over plain HTTP (off loopback, without
	// TLS). It caps the lifetime at TokenLifetimeCapped.
	Cleartext bool
	// Elevated: the request was made from the admin session asking for a
	// scope above read (D-036 safeguard 3: an operate-or-above token
	// `flyball login` mints lives at most 30 d, whatever the config
	// says). It caps the lifetime the same as Cleartext/KindAgent, but is
	// not itself persisted on the record -- it says why the cap applied
	// at creation, not a fact about the token afterwards.
	Elevated bool
}

// TokenLifetime is the lifetime a token of kind, created over cleartext or
// not, or from an elevated admin session or not, gets when requested is
// asked for: requested (0 = lt.Default) capped at min(TokenLifetimeCapped,
// lt.Max) for cleartext, KindAgent or an elevated session -- the fixed
// 30-day cap, tightened further if lt.Max is smaller -- and at lt.Max
// otherwise. There is no "never".
func TokenLifetime(lt Lifetimes, kind string, cleartext, elevated bool, requested time.Duration) (time.Duration, error) {
	if requested < 0 {
		return 0, fmt.Errorf("a token's lifetime must be positive, not %v", requested)
	}
	limit := lt.Max
	if cleartext || kind == KindAgent || elevated {
		limit = min(TokenLifetimeCapped, lt.Max)
	}
	if requested == 0 {
		requested = lt.Default
	}
	return min(requested, limit), nil
}

// IsToken reports whether s has a named token's shape (not whether it is
// held): the front's provider chain uses it to say NotMine before hashing.
func IsToken(s string) bool {
	body, ok := strings.CutPrefix(s, TokenPrefix)
	if !ok || len(body) != 43 {
		return false
	}
	for i := 0; i < len(body); i++ {
		c := body[i]
		if !('A' <= c && c <= 'Z' || 'a' <= c && c <= 'z' || '0' <= c && c <= '9' || c == '-' || c == '_') {
			return false
		}
	}
	return true
}

// TokensOptions configures a Tokens.
type TokensOptions struct {
	// Now is the clock for creation, expiry and last use: wall time, since a
	// token outlives the process (a Pi without an RTC can mis-date tokens
	// until NTP answers). nil: time.Now.
	Now func() time.Time
	// OnEnd is called, outside the store's lock, for each token that stops
	// working: revoked through this handle, found gone from the file (revoked
	// by another process: the CLI), or found expired by Sweep, the last once
	// per token. The front cancels that token's websockets and streams.
	OnEnd func(id string)
	// Lifetimes is the effective default and max lifetime new tokens get
	// (Create); the zero value uses the built-ins (DefaultLifetimes()). The
	// caller resolves `tokens:` config with ResolveLifetimes before this.
	Lifetimes Lifetimes
}

// tokensFile is the file's shape. format_version 1; any other is refused.
type tokensFile struct {
	FormatVersion int      `json:"format_version"`
	Tokens        []record `json:"tokens"`
}

// record is a row on disk: a Token plus the SHA-256 (hex) of its secret.
type record struct {
	ID        string     `json:"id"`
	Name      string     `json:"name"`
	Hash      string     `json:"hash"`
	Scopes    []string   `json:"scopes"`
	Kind      string     `json:"kind"`
	Issuer    string     `json:"issuer,omitempty"`
	Cleartext bool       `json:"cleartext,omitempty"`
	Created   time.Time  `json:"created"`
	Expires   time.Time  `json:"expires"`
	LastUsed  *time.Time `json:"last_used"`
}

type entry struct {
	rec  record
	hash [sha256.Size]byte
}

// Tokens is the named-tokens file: `<data_dir>/front/tokens.json` for
// flyballd, `$XDG_STATE_HOME/flyball/front-<id>/tokens.json` for
// `flyball run` (the caller picks the path). Mode 0600 in a 0700 directory,
// set by this code whatever the umask; written whole to a temporary file and
// renamed into place; each read-modify-write holds an flock on
// `<path>.lock`, so the front and the CLI (`flyball token`, which writes the
// file offline) never lose each other's update. Only a token's SHA-256 is
// kept.
//
// A Tokens re-reads the file whenever it has changed (checked by stat at
// every call), so a token the CLI creates or revokes counts at the front's
// next Lookup. It is safe for concurrent use.
type Tokens struct {
	path      string
	now       func() time.Time
	onEnd     func(string)
	compare   func(a, b []byte) int
	lifetimes Lifetimes

	mu sync.Mutex
	// held is the file the rows came from, kept open so that its inode
	// cannot be freed and reused by a later write while this handle
	// compares against it; info is its stat when read. nil: no file.
	held     *os.File
	info     os.FileInfo
	rows     []entry
	lastUsed map[string]time.Time // uses not yet written back
	expired  map[string]bool      // ids whose expiry OnEnd has reported
}

// OpenTokens loads the file at path. A missing file is an empty store (it
// is created at the first Create); an unreadable or malformed one is an
// error.
func OpenTokens(path string, o TokensOptions) (*Tokens, error) {
	t := &Tokens{
		path: path, now: o.Now, onEnd: o.OnEnd, compare: subtle.ConstantTimeCompare,
		lifetimes: o.Lifetimes,
		lastUsed:  map[string]time.Time{}, expired: map[string]bool{},
	}
	if t.now == nil {
		t.now = time.Now
	}
	if t.lifetimes == (Lifetimes{}) {
		t.lifetimes = DefaultLifetimes()
	}
	t.mu.Lock()
	_, err := t.refreshLocked()
	t.mu.Unlock()
	if err != nil {
		return nil, err
	}
	return t, nil
}

// Close releases the file this handle holds open. The handle still works
// afterwards, re-reading the file at its next call.
func (t *Tokens) Close() error {
	t.mu.Lock()
	defer t.mu.Unlock()
	var err error
	if t.held != nil {
		err = t.held.Close()
	}
	t.held, t.info = nil, nil
	return err
}

// Create issues a token and returns its secret, shown once: the file keeps
// only its hash.
func (t *Tokens) Create(n NewToken) (secret string, _ Token, _ error) {
	if err := checkName(n.Name); err != nil {
		return "", Token{}, err
	}
	if n.Kind == "" {
		n.Kind = KindService
	}
	if n.Kind != KindHuman && n.Kind != KindService && n.Kind != KindAgent {
		return "", Token{}, fmt.Errorf("token kind %q: want %s, %s or %s", n.Kind, KindHuman, KindService, KindAgent)
	}
	life, err := TokenLifetime(t.lifetimes, n.Kind, n.Cleartext, n.Elevated, n.ExpiresIn)
	if err != nil {
		return "", Token{}, err
	}
	raw, id := make([]byte, 32), make([]byte, 8)
	if _, err := rand.Read(raw); err != nil {
		return "", Token{}, err
	}
	if _, err := rand.Read(id); err != nil {
		return "", Token{}, err
	}
	secret = TokenPrefix + b64(raw)
	sum := sha256.Sum256([]byte(secret))
	now := t.now().UTC()
	rec := record{
		ID: hex.EncodeToString(id), Name: n.Name, Hash: hex.EncodeToString(sum[:]),
		Scopes: nonNil(slices.Clone(n.Scopes)), Kind: n.Kind, Issuer: n.Issuer, Cleartext: n.Cleartext,
		Created: now, Expires: now.Add(life),
	}
	t.mu.Lock()
	ended, err := t.writeLocked(func(f *tokensFile) error {
		f.Tokens = append(f.Tokens, rec)
		return nil
	})
	t.mu.Unlock()
	t.end(ended)
	if err != nil {
		return "", Token{}, err
	}
	return secret, rec.token(nil), nil
}

// Lookup returns the token secret names and records its use. Every held
// hash is compared, in constant time and without an early exit. It returns
// ErrNoToken or ErrTokenExpired for a secret that does not work, and any
// other error for a store failure.
func (t *Tokens) Lookup(secret string) (Token, error) {
	if !IsToken(secret) {
		return Token{}, ErrNoToken
	}
	sum := sha256.Sum256([]byte(secret))
	now := t.now().UTC()
	t.mu.Lock()
	ended, err := t.refreshLocked()
	if err != nil {
		t.mu.Unlock()
		t.end(ended)
		return Token{}, err
	}
	found := -1
	for i := range t.rows {
		if t.compare(sum[:], t.rows[i].hash[:]) == 1 {
			found = i
		}
	}
	var out Token
	switch {
	case found < 0:
		err = ErrNoToken
	case !now.Before(t.rows[found].rec.Expires):
		err = ErrTokenExpired
	default:
		rec := t.rows[found].rec
		t.lastUsed[rec.ID] = now
		out = rec.token(&now)
	}
	t.mu.Unlock()
	t.end(ended)
	return out, err
}

// List returns every token in the file, expired ones included, oldest
// first.
func (t *Tokens) List() ([]Token, error) {
	t.mu.Lock()
	ended, err := t.refreshLocked()
	var out []Token
	if err == nil {
		out = make([]Token, 0, len(t.rows))
		for _, e := range t.rows {
			var used *time.Time
			if at, ok := t.lastUsed[e.rec.ID]; ok {
				used = &at
			}
			out = append(out, e.rec.token(used))
		}
	}
	t.mu.Unlock()
	t.end(ended)
	slices.SortStableFunc(out, func(a, b Token) int { return a.Created.Compare(b.Created) })
	return out, err
}

// Revoke deletes the token with id from the file. It counts at once: the
// next Lookup, here or in any process holding the file, refuses it. OnEnd
// is called for it.
func (t *Tokens) Revoke(id string) error {
	t.mu.Lock()
	ended, err := t.writeLocked(func(f *tokensFile) error {
		i := slices.IndexFunc(f.Tokens, func(r record) bool { return r.ID == id })
		if i < 0 {
			return ErrTokenNotFound
		}
		f.Tokens = slices.Delete(f.Tokens, i, i+1)
		return nil
	})
	t.mu.Unlock()
	t.end(ended)
	return err
}

// Sweep re-reads the file if it changed (reporting tokens revoked
// elsewhere), reports tokens that have expired since the last Sweep, and
// writes back last uses that have moved by LastUsedCoalesce or more. The
// front runs it about once a second, so that a revoked or expired token's
// open websockets close within a second.
func (t *Tokens) Sweep() error {
	now := t.now()
	t.mu.Lock()
	ended, err := t.refreshLocked()
	if err == nil {
		for _, e := range t.rows {
			if !now.Before(e.rec.Expires) && !t.expired[e.rec.ID] {
				t.expired[e.rec.ID] = true
				ended = append(ended, e.rec.ID)
			}
		}
		if t.flushDueLocked() {
			var more []string
			more, err = t.writeLocked(func(*tokensFile) error { return nil })
			ended = append(ended, more...)
		}
	}
	t.mu.Unlock()
	t.end(ended)
	return err
}

// flushDueLocked: whether any last use has moved far enough from the file's
// to be worth a write.
func (t *Tokens) flushDueLocked() bool {
	for _, e := range t.rows {
		at, ok := t.lastUsed[e.rec.ID]
		if ok && (e.rec.LastUsed == nil || at.Sub(*e.rec.LastUsed) >= LastUsedCoalesce) {
			return true
		}
	}
	return false
}

// refreshLocked re-reads the file if it is not the one the rows came from,
// and returns the ids that were held and are now gone.
func (t *Tokens) refreshLocked() ([]string, error) {
	info, err := os.Stat(t.path)
	if errors.Is(err, os.ErrNotExist) {
		return t.replaceLocked(nil, nil, nil), nil
	}
	if err != nil {
		return nil, fmt.Errorf("tokens file: %w", err)
	}
	if t.info != nil && os.SameFile(t.info, info) && t.info.ModTime().Equal(info.ModTime()) && t.info.Size() == info.Size() {
		return nil, nil
	}
	f, fh, info, err := readTokensFile(t.path)
	if err != nil {
		return nil, err
	}
	return t.replaceLocked(f, fh, info), nil
}

// writeLocked is a read-modify-write under the cross-process lock: it
// reads the file as it is now, merges in the uses not yet written, applies
// change, and writes the result atomically. An error from change aborts the
// write and is returned as is.
func (t *Tokens) writeLocked(change func(*tokensFile) error) ([]string, error) {
	dir := filepath.Dir(t.path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("tokens dir: %w", err)
	}
	unlock, err := lockFile(t.path + ".lock")
	if err != nil {
		return nil, fmt.Errorf("tokens lock: %w", err)
	}
	defer unlock()
	f, fh, _, err := readTokensFile(t.path)
	if err != nil {
		return nil, err
	}
	if fh != nil {
		fh.Close()
	}
	if f == nil {
		f = &tokensFile{FormatVersion: 1}
	}
	for i := range f.Tokens {
		r := &f.Tokens[i]
		if at, ok := t.lastUsed[r.ID]; ok && (r.LastUsed == nil || at.After(*r.LastUsed)) {
			r.LastUsed = &at
		}
	}
	if err := change(f); err != nil {
		return nil, err
	}
	if err := writeTokensFile(t.path, f); err != nil {
		return nil, err
	}
	// Still under the lock, so no other writer has replaced it since.
	fh, err = os.Open(t.path)
	if err != nil {
		return nil, fmt.Errorf("tokens file: %w", err)
	}
	info, err := fh.Stat()
	if err != nil {
		fh.Close()
		return nil, fmt.Errorf("tokens file: %w", err)
	}
	return t.replaceLocked(f, fh, info), nil
}

// replaceLocked makes f (nil: no file), read from fh, the held rows and
// returns the ids that were held and are not in f. Uses not yet written back
// are kept for the ids still present.
func (t *Tokens) replaceLocked(f *tokensFile, fh *os.File, info os.FileInfo) []string {
	next := []entry{}
	if f != nil {
		for _, r := range f.Tokens {
			e := entry{rec: r}
			hex.Decode(e.hash[:], []byte(r.Hash)) // checked by readTokensFile / Create
			next = append(next, e)
		}
	}
	present := map[string]bool{}
	for _, e := range next {
		present[e.rec.ID] = true
		if at, ok := t.lastUsed[e.rec.ID]; ok && e.rec.LastUsed != nil && !at.After(*e.rec.LastUsed) {
			delete(t.lastUsed, e.rec.ID) // the file has caught up
		}
	}
	var gone []string
	for _, e := range t.rows {
		if !present[e.rec.ID] {
			gone = append(gone, e.rec.ID)
			delete(t.lastUsed, e.rec.ID)
			delete(t.expired, e.rec.ID)
		}
	}
	if t.held != nil && t.held != fh {
		t.held.Close()
	}
	t.rows, t.held, t.info = next, fh, info
	return gone
}

func (t *Tokens) end(ids []string) {
	if t.onEnd == nil {
		return
	}
	for _, id := range ids {
		t.onEnd(id)
	}
}

// readTokensFile reads and checks the file, and returns it still open with
// its stat; a missing file is all nil.
func readTokensFile(path string) (_ *tokensFile, _ *os.File, _ os.FileInfo, err error) {
	fh, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil, nil, nil
	}
	if err != nil {
		return nil, nil, nil, fmt.Errorf("tokens file: %w", err)
	}
	defer func() {
		if err != nil {
			fh.Close()
		}
	}()
	info, err := fh.Stat()
	if err != nil {
		return nil, nil, nil, fmt.Errorf("tokens file: %w", err)
	}
	var f tokensFile
	if err := json.NewDecoder(fh).Decode(&f); err != nil {
		return nil, nil, nil, fmt.Errorf("tokens file %s: %w", path, err)
	}
	if f.FormatVersion != 1 {
		return nil, nil, nil, fmt.Errorf("tokens file %s: format_version %d, want 1", path, f.FormatVersion)
	}
	ids := map[string]bool{}
	for _, r := range f.Tokens {
		raw, err := hex.DecodeString(r.Hash)
		if r.ID == "" || ids[r.ID] || err != nil || len(raw) != sha256.Size || r.Expires.IsZero() {
			return nil, nil, nil, fmt.Errorf("tokens file %s: bad row %q", path, r.ID)
		}
		ids[r.ID] = true
	}
	return &f, fh, info, nil
}

// writeTokensFile writes f to a temporary file in path's directory, sets
// 0600 on it explicitly (not left to the umask), syncs it and renames it
// over path, then syncs the directory.
func writeTokensFile(path string, f *tokensFile) error {
	data, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".*")
	if err != nil {
		return fmt.Errorf("tokens file: %w", err)
	}
	done := false
	defer func() {
		if !done {
			tmp.Close()
			os.Remove(tmp.Name())
		}
	}()
	if err := tmp.Chmod(0o600); err != nil {
		return fmt.Errorf("tokens file: %w", err)
	}
	if _, err := tmp.Write(data); err != nil {
		return fmt.Errorf("tokens file: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		return fmt.Errorf("tokens file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("tokens file: %w", err)
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return fmt.Errorf("tokens file: %w", err)
	}
	done = true
	if d, err := os.Open(dir); err == nil {
		d.Sync()
		d.Close()
	}
	return nil
}

func checkName(name string) error {
	if name == "" {
		return errors.New("a token needs a name")
	}
	if !utf8.ValidString(name) || utf8.RuneCountInString(name) > tokenNameMax {
		return fmt.Errorf("a token's name is at most %d characters of UTF-8", tokenNameMax)
	}
	for _, r := range name {
		if unicode.IsControl(r) || unicode.In(r, unicode.Cf, unicode.Zl, unicode.Zp) {
			return fmt.Errorf("a token's name has no control or format characters (found %U)", r)
		}
	}
	return nil
}

func (r record) token(lastUsed *time.Time) Token {
	used := r.LastUsed
	if lastUsed != nil && (used == nil || lastUsed.After(*used)) {
		used = lastUsed
	}
	if used != nil {
		at := *used
		used = &at
	}
	return Token{
		ID: r.ID, Name: r.Name, Scopes: nonNil(slices.Clone(r.Scopes)), Kind: r.Kind,
		Created: r.Created, Expires: r.Expires, LastUsed: used, Issuer: r.Issuer, Cleartext: r.Cleartext,
	}
}

func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}
