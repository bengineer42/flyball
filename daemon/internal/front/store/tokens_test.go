package store

import (
	"bytes"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

type tokenFixture struct {
	path  string
	clock *fakeClock
	gone  *ended
	tok   *Tokens
}

func newTokenFixture(t *testing.T) *tokenFixture {
	t.Helper()
	f := &tokenFixture{
		path:  filepath.Join(t.TempDir(), "front", "tokens.json"),
		clock: newFakeClock(),
		gone:  &ended{},
	}
	f.tok = f.open(t)
	return f
}

// open is another handle on the same file: another process, as far as the
// store can tell (the CLI's `flyball token`, with the front running).
func (f *tokenFixture) open(t *testing.T) *Tokens {
	t.Helper()
	tok, err := OpenTokens(f.path, TokensOptions{Now: f.clock.Now, OnEnd: f.gone.record})
	if err != nil {
		t.Fatalf("OpenTokens: %v", err)
	}
	t.Cleanup(func() { tok.Close() })
	return tok
}

func (f *tokenFixture) create(t *testing.T, n NewToken) (string, Token) {
	t.Helper()
	if n.Name == "" {
		n.Name = "ci"
	}
	secret, row, err := f.tok.Create(n)
	if err != nil {
		t.Fatalf("Create(%+v): %v", n, err)
	}
	return secret, row
}

func TestTokensFile(t *testing.T) {
	t.Run("mode 0600 in a 0700 dir", func(t *testing.T) {
		f := newTokenFixture(t)
		f.create(t, NewToken{})
		for path, want := range map[string]os.FileMode{f.path: 0o600, filepath.Dir(f.path): 0o700 | os.ModeDir} {
			info, err := os.Stat(path)
			if err != nil {
				t.Fatal(err)
			}
			if info.Mode() != want {
				t.Errorf("%s mode %v; want %v", path, info.Mode(), want)
			}
		}
	})

	t.Run("a secret shaped fbt1_ + 43 base64url", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, row := f.create(t, NewToken{})
		body, ok := strings.CutPrefix(secret, TokenPrefix)
		raw, err := base64.RawURLEncoding.DecodeString(body)
		if !ok || len(body) != 43 || err != nil || len(raw) != 32 {
			t.Errorf("secret %q is not %s + 43 base64url chars (32 bytes)", secret, TokenPrefix)
		}
		if !IsToken(secret) || IsToken("Bearer "+secret) || IsToken(body) {
			t.Errorf("IsToken misjudges the shape")
		}
		if strings.Contains(secret, row.ID) || len(row.ID) < 16 {
			t.Errorf("id %q is short or part of the secret", row.ID)
		}
	})

	t.Run("hash only at rest", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, _ := f.create(t, NewToken{})
		data, err := os.ReadFile(f.path)
		if err != nil {
			t.Fatal(err)
		}
		body := strings.TrimPrefix(secret, TokenPrefix)
		if bytes.Contains(data, []byte(body)) || bytes.Contains(data, []byte(body[:20])) {
			t.Errorf("the token (or a part of it) is in the file:\n%s", data)
		}
		raw, _ := base64.RawURLEncoding.DecodeString(body)
		if bytes.Contains(data, []byte(fmt.Sprintf("%x", raw))) {
			t.Errorf("the token's bytes, in hex, are in the file")
		}
	})

	t.Run("lookup by hash", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, row := f.create(t, NewToken{Scopes: []string{"read"}})
		got, err := f.tok.Lookup(secret)
		if err != nil || got.ID != row.ID {
			t.Fatalf("Lookup = %+v, %v; want row %s", got, err, row.ID)
		}
		last := secret[len(secret)-1]
		flipped := secret[:len(secret)-1] + string("AB"[boolIndex(last == 'A')])
		for _, bad := range []string{flipped, "", "fbt1_", strings.TrimPrefix(secret, TokenPrefix), secret + "A"} {
			if _, err := f.tok.Lookup(bad); !errors.Is(err, ErrNoToken) {
				t.Errorf("Lookup(%q) = %v; want ErrNoToken", bad, err)
			}
		}
		// A fresh handle finds it from the file alone.
		if got, err := f.open(t).Lookup(secret); err != nil || got.ID != row.ID {
			t.Errorf("Lookup on a fresh handle = %+v, %v", got, err)
		}
	})

	t.Run("hashes compared in constant time, every row", func(t *testing.T) {
		f := newTokenFixture(t)
		var first string
		for i := range 5 {
			secret, _ := f.create(t, NewToken{Name: fmt.Sprint("t", i)})
			if i == 0 {
				first = secret
			}
		}
		calls := 0
		f.tok.compare = func(a, b []byte) int { calls++; return subtle.ConstantTimeCompare(a, b) }
		if _, err := f.tok.Lookup(first); err != nil {
			t.Fatal(err)
		}
		if calls != 5 {
			t.Errorf("Lookup of the first row compared %d hashes; want all 5 (no early exit)", calls)
		}
	})

	t.Run("expiry is mandatory, defaulted and capped", func(t *testing.T) {
		f := newTokenFixture(t)
		now := f.clock.Now()
		day := 24 * time.Hour
		for _, c := range []struct {
			n    NewToken
			want time.Duration
		}{
			{NewToken{}, TokenLifetimeDefault},
			{NewToken{Kind: "service"}, 90 * day},
			{NewToken{Cleartext: true}, 30 * day},
			{NewToken{Kind: "agent"}, 30 * day},
			{NewToken{Kind: "agent", ExpiresIn: 60 * day}, 30 * day},
			{NewToken{Cleartext: true, ExpiresIn: 7 * day}, 7 * day},
			{NewToken{ExpiresIn: 200 * day}, 200 * day},
			{NewToken{ExpiresIn: 10 * 365 * day}, TokenLifetimeMax},
			{NewToken{Kind: "human", ExpiresIn: time.Hour}, time.Hour},
		} {
			_, row := f.create(t, c.n)
			if got := row.Expires.Sub(now); got != c.want {
				t.Errorf("%+v: lifetime %v; want %v", c.n, got, c.want)
			}
			if row.Expires.IsZero() {
				t.Errorf("%+v: no expiry", c.n)
			}
		}
		if _, _, err := f.tok.Create(NewToken{Name: "x", ExpiresIn: -time.Hour}); err == nil {
			t.Errorf("a negative lifetime was accepted")
		}
		if TokenLifetimeDefault != 90*day || TokenLifetimeCapped != 30*day {
			t.Errorf("lifetimes moved: default %v, capped %v", TokenLifetimeDefault, TokenLifetimeCapped)
		}
	})

	t.Run("an expired token is refused and ends once", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, row := f.create(t, NewToken{ExpiresIn: time.Hour})
		f.clock.Advance(time.Hour)
		if _, err := f.tok.Lookup(secret); !errors.Is(err, ErrTokenExpired) {
			t.Errorf("Lookup at expiry = %v; want ErrTokenExpired", err)
		}
		if err := f.tok.Sweep(); err != nil {
			t.Fatal(err)
		}
		if err := f.tok.Sweep(); err != nil {
			t.Fatal(err)
		}
		if got := f.gone.list(); !slices.Equal(got, []string{row.ID}) {
			t.Errorf("OnEnd calls = %v; want exactly [%s]", got, row.ID)
		}
		// Kept in the file, so `list` shows it until someone revokes it.
		if rows, _ := f.tok.List(); len(rows) != 1 {
			t.Errorf("List after expiry = %d rows; want the expired row kept", len(rows))
		}
	})

	t.Run("revoke takes effect at once and fires OnEnd", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, row := f.create(t, NewToken{})
		_, other := f.create(t, NewToken{Name: "keep"})
		if err := f.tok.Revoke(row.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := f.tok.Lookup(secret); !errors.Is(err, ErrNoToken) {
			t.Errorf("Lookup right after Revoke = %v; want ErrNoToken", err)
		}
		if got := f.gone.list(); !slices.Equal(got, []string{row.ID}) {
			t.Errorf("OnEnd calls = %v; want [%s]", got, row.ID)
		}
		if err := f.tok.Revoke(row.ID); !errors.Is(err, ErrTokenNotFound) {
			t.Errorf("second Revoke = %v; want ErrTokenNotFound", err)
		}
		rows, _ := f.tok.List()
		if len(rows) != 1 || rows[0].ID != other.ID {
			t.Errorf("List after Revoke = %+v; want only %s", rows, other.ID)
		}
	})

	t.Run("revoke by another process takes effect at the next lookup", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, row := f.create(t, NewToken{})
		if _, err := f.tok.Lookup(secret); err != nil {
			t.Fatal(err)
		}
		cli := f.open(t)
		if err := cli.Revoke(row.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := f.tok.Lookup(secret); !errors.Is(err, ErrNoToken) {
			t.Errorf("the front's Lookup after the CLI's revoke = %v; want ErrNoToken", err)
		}
		if err := f.tok.Sweep(); err != nil {
			t.Fatal(err)
		}
		// Once from the CLI's own handle, once from the front's when it saw the file change.
		if got := f.gone.list(); !slices.Equal(got, []string{row.ID, row.ID}) {
			t.Errorf("OnEnd calls = %v; want [%s %s] (one per handle)", got, row.ID, row.ID)
		}
	})

	t.Run("a token created by another process is found", func(t *testing.T) {
		f := newTokenFixture(t)
		f.create(t, NewToken{Name: "first"})
		secret, row, err := f.open(t).Create(NewToken{Name: "offline"})
		if err != nil {
			t.Fatal(err)
		}
		if got, err := f.tok.Lookup(secret); err != nil || got.ID != row.ID {
			t.Errorf("Lookup of a CLI-made token = %+v, %v", got, err)
		}
		if rows, _ := f.tok.List(); len(rows) != 2 {
			t.Errorf("List = %d rows; want 2 (nothing lost to the other writer)", len(rows))
		}
	})

	t.Run("last_used is kept, and written back coalesced", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, row := f.create(t, NewToken{})
		f.clock.Advance(time.Minute)
		used := f.clock.Now()
		if got, _ := f.tok.Lookup(secret); got.LastUsed == nil || !got.LastUsed.Equal(used) {
			t.Errorf("Lookup's LastUsed = %v; want %v", got.LastUsed, used)
		}
		if onDisk(t, f.path, row.ID).LastUsed != nil {
			t.Errorf("last_used written on every lookup; want it coalesced")
		}
		if err := f.tok.Sweep(); err != nil {
			t.Fatal(err)
		}
		if got := onDisk(t, f.path, row.ID).LastUsed; got == nil || !got.Equal(used) {
			t.Errorf("last_used on disk after Sweep = %v; want %v", got, used)
		}
		before, _ := os.ReadFile(f.path)
		f.clock.Advance(LastUsedCoalesce / 2)
		f.tok.Lookup(secret)
		f.tok.Sweep()
		if after, _ := os.ReadFile(f.path); !bytes.Equal(before, after) {
			t.Errorf("file rewritten for a last_used %v newer; want %v coalesced", LastUsedCoalesce/2, LastUsedCoalesce)
		}
		f.clock.Advance(LastUsedCoalesce)
		f.tok.Lookup(secret)
		f.tok.Sweep()
		if got := onDisk(t, f.path, row.ID).LastUsed; got == nil || !got.Equal(f.clock.Now()) {
			t.Errorf("last_used on disk = %v; want %v", got, f.clock.Now())
		}
		rows, _ := f.tok.List()
		if rows[0].LastUsed == nil || !rows[0].LastUsed.Equal(f.clock.Now()) {
			t.Errorf("List's last_used = %v", rows[0].LastUsed)
		}
	})

	t.Run("scopes, kind and issuer stored as given", func(t *testing.T) {
		f := newTokenFixture(t)
		scopes := []string{"read:*", "operate:furnace", "something D-034 invents"}
		_, row := f.create(t, NewToken{Name: "mcp", Scopes: scopes, Kind: "agent", Issuer: "proxy:https://id.example#u42"})
		scopes[0] = "admin"
		rows, err := f.open(t).List()
		if err != nil || len(rows) != 1 {
			t.Fatalf("List = %v, %v", rows, err)
		}
		got := rows[0]
		if !slices.Equal(got.Scopes, []string{"read:*", "operate:furnace", "something D-034 invents"}) ||
			got.Kind != "agent" || got.Issuer != "proxy:https://id.example#u42" || got.Name != "mcp" || got.ID != row.ID {
			t.Errorf("row read back = %+v", got)
		}
		_, plain := f.create(t, NewToken{Name: "bare"})
		if plain.Kind != "service" || plain.Scopes == nil {
			t.Errorf("defaults: kind %q, scopes %v; want service, []", plain.Kind, plain.Scopes)
		}
	})

	t.Run("the wire row", func(t *testing.T) {
		f := newTokenFixture(t)
		_, row := f.create(t, NewToken{Scopes: []string{"read"}, Issuer: "local:admin", Cleartext: true})
		data, _ := json.Marshal(row)
		var keys map[string]any
		_ = json.Unmarshal(data, &keys)
		got := slices.Sorted(func(yield func(string) bool) {
			for k := range keys {
				if !yield(k) {
					return
				}
			}
		})
		if want := []string{"created", "expires", "id", "kind", "last_used", "name", "scopes"}; !slices.Equal(got, want) {
			t.Errorf("Token's JSON keys = %v; want the §WP0-8 row %v", got, want)
		}
	})

	t.Run("create refuses a bad name or kind", func(t *testing.T) {
		f := newTokenFixture(t)
		for _, n := range []NewToken{
			{Name: ""},
			{Name: "a\nb"},
			{Name: "a b"},
			{Name: "\x1b[31mred"},
			{Name: strings.Repeat("x", 65)},
			{Name: "ok", Kind: "root"},
		} {
			if _, _, err := f.tok.Create(n); err == nil {
				t.Errorf("Create(%q, kind %q) accepted", n.Name, n.Kind)
			}
		}
	})

	t.Run("a bad file is an error, not an empty store", func(t *testing.T) {
		f := newTokenFixture(t)
		secret, _ := f.create(t, NewToken{})
		for _, body := range []string{"{not json", `{"version":2,"tokens":[]}`, `{"version":1,"tokens":[{"id":"x","hash":"zz"}]}`} {
			if err := os.WriteFile(f.path, []byte(body), 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := f.tok.Lookup(secret); err == nil || errors.Is(err, ErrNoToken) {
				t.Errorf("Lookup over %q = %v; want a store error (503), not ErrNoToken", body, err)
			}
			if _, err := OpenTokens(f.path, TokensOptions{}); err == nil {
				t.Errorf("OpenTokens over %q = nil", body)
			}
			if _, _, err := f.tok.Create(NewToken{Name: "x"}); err == nil {
				t.Errorf("Create over %q overwrote it", body)
			}
		}
	})

	t.Run("a missing file is an empty store", func(t *testing.T) {
		tok, err := OpenTokens(filepath.Join(t.TempDir(), "none", "tokens.json"), TokensOptions{})
		if err != nil {
			t.Fatal(err)
		}
		if rows, err := tok.List(); err != nil || len(rows) != 0 {
			t.Errorf("List = %v, %v; want empty", rows, err)
		}
		if _, err := tok.Lookup(TokenPrefix + strings.Repeat("A", 43)); !errors.Is(err, ErrNoToken) {
			t.Errorf("Lookup = %v; want ErrNoToken", err)
		}
	})

	t.Run("atomic: a reader never sees a partial file", func(t *testing.T) {
		f := newTokenFixture(t)
		f.create(t, NewToken{})
		stop := make(chan struct{})
		var readers sync.WaitGroup
		readers.Add(1)
		go func() {
			defer readers.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				data, err := os.ReadFile(f.path)
				if err != nil {
					t.Errorf("read: %v", err)
					return
				}
				var v tokensFile
				if err := json.Unmarshal(data, &v); err != nil {
					t.Errorf("a reader saw a partial file (%d bytes): %v", len(data), err)
					return
				}
			}
		}()
		for i := range 50 {
			_, row := f.create(t, NewToken{Name: fmt.Sprint("n", i), Scopes: []string{strings.Repeat("s", i*50)}})
			if i%2 == 0 {
				f.tok.Revoke(row.ID)
			}
		}
		close(stop)
		readers.Wait()
		entries, _ := os.ReadDir(filepath.Dir(f.path))
		for _, e := range entries {
			if e.Name() != "tokens.json" && e.Name() != "tokens.json.lock" {
				t.Errorf("left behind: %s", e.Name())
			}
		}
	})
}

func TestTokensConcurrent(t *testing.T) {
	f := newTokenFixture(t)
	cli := f.open(t)
	var wg sync.WaitGroup
	for g := range 6 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			h := f.tok
			if g%2 == 1 {
				h = cli
			}
			for i := range 15 {
				secret, row, err := h.Create(NewToken{Name: fmt.Sprint("g", g, "-", i)})
				if err != nil {
					t.Error(err)
					return
				}
				if _, err := f.tok.Lookup(secret); err != nil {
					t.Errorf("Lookup of a just-made token: %v", err)
				}
				h.List()
				h.Sweep()
				if i%3 == 0 {
					if err := h.Revoke(row.ID); err != nil {
						t.Errorf("Revoke: %v", err)
					}
					if _, err := f.tok.Lookup(secret); !errors.Is(err, ErrNoToken) {
						t.Errorf("Lookup after Revoke = %v", err)
					}
				}
			}
		}()
	}
	wg.Wait()
	rows, err := f.open(t).List()
	if err != nil {
		t.Fatal(err)
	}
	if want := 6 * 10; len(rows) != want {
		t.Errorf("%d rows at the end; want %d (a lost update between handles?)", len(rows), want)
	}
}

func onDisk(t *testing.T, path, id string) record {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var v tokensFile
	if err := json.Unmarshal(data, &v); err != nil {
		t.Fatal(err)
	}
	for _, r := range v.Tokens {
		if r.ID == id {
			return r
		}
	}
	t.Fatalf("no row %s on disk", id)
	return record{}
}

func boolIndex(b bool) int {
	if b {
		return 1
	}
	return 0
}

// TestConfiguredLifetimes: a front given a `tokens:` block's effective
// Lifetimes (store.ResolveLifetimes's result) applies it in Create --
// default applied, a request above the configured max clamped, and the
// fixed agent/cleartext 30-day cap tightened further when the configured
// max is smaller than it (never loosened above it).
func TestConfiguredLifetimes(t *testing.T) {
	clock := newFakeClock()
	day := 24 * time.Hour

	t.Run("default and max from config", func(t *testing.T) {
		tok, err := OpenTokens(filepath.Join(t.TempDir(), "tokens.json"),
			TokensOptions{Now: clock.Now, Lifetimes: Lifetimes{Default: 5 * day, Max: 20 * day}})
		if err != nil {
			t.Fatal(err)
		}
		defer tok.Close()
		now := clock.Now()

		_, row, err := tok.Create(NewToken{Name: "default"})
		if err != nil {
			t.Fatal(err)
		}
		if got := row.Expires.Sub(now); got != 5*day {
			t.Errorf("no expires_in: lifetime %v, want the configured default 5d", got)
		}

		_, row, err = tok.Create(NewToken{Name: "above-max", ExpiresIn: 100 * day})
		if err != nil {
			t.Fatal(err)
		}
		if got := row.Expires.Sub(now); got != 20*day {
			t.Errorf("above configured max: lifetime %v, want clamped to 20d", got)
		}
	})

	t.Run("agent and cleartext cap is min(30d, configured max)", func(t *testing.T) {
		tok, err := OpenTokens(filepath.Join(t.TempDir(), "tokens.json"),
			TokensOptions{Now: clock.Now, Lifetimes: Lifetimes{Default: 5 * day, Max: 10 * day}})
		if err != nil {
			t.Fatal(err)
		}
		defer tok.Close()
		now := clock.Now()

		_, row, err := tok.Create(NewToken{Name: "agent", Kind: KindAgent, ExpiresIn: 100 * day})
		if err != nil {
			t.Fatal(err)
		}
		if got := row.Expires.Sub(now); got != 10*day {
			t.Errorf("agent, max 10d < the fixed 30d cap: lifetime %v, want 10d", got)
		}

		_, row, err = tok.Create(NewToken{Name: "cleartext", Cleartext: true, ExpiresIn: 100 * day})
		if err != nil {
			t.Fatal(err)
		}
		if got := row.Expires.Sub(now); got != 10*day {
			t.Errorf("cleartext, max 10d < the fixed 30d cap: lifetime %v, want 10d", got)
		}
	})

	t.Run("agent cap stays at the fixed 30d when the configured max is larger", func(t *testing.T) {
		tok, err := OpenTokens(filepath.Join(t.TempDir(), "tokens.json"),
			TokensOptions{Now: clock.Now, Lifetimes: Lifetimes{Default: 90 * day, Max: 100 * day}})
		if err != nil {
			t.Fatal(err)
		}
		defer tok.Close()
		now := clock.Now()

		_, row, err := tok.Create(NewToken{Name: "agent", Kind: KindAgent, ExpiresIn: 100 * day})
		if err != nil {
			t.Fatal(err)
		}
		if got := row.Expires.Sub(now); got != TokenLifetimeCapped {
			t.Errorf("agent, max 100d > the fixed 30d cap: lifetime %v, want the fixed %v", got, TokenLifetimeCapped)
		}
	})
}
