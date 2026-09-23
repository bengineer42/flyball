// Auth support: bearer tokens for talking to a front-protected runner or
// daemon (daemon/internal/front's password shape).
//
// One mechanism now, not two: a named token, sent as `Authorization:
// Bearer T`. `flyball login` no longer keeps a session cookie -- fronts
// keep sessions in memory only, and a front restart would silently sign
// the CLI out, so `flyball login` mints itself a real token instead
// (daemon/internal/front/auth.go's POST /api/auth/tokens) and persists
// *that*, the same as a machine's own --token/FLYBALL_TOKEN. See
// login.go for the two-step flow (password -> session -> token) and
// resolve.go for where the saved token is picked back up
// (Target.AuthHeaders).
package client

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Token is the bearer token to send, if any: --token wins over
// FLYBALL_TOKEN, matching the old Python client's precedence
// (flyball.interfaces.client.rig.Rig.__init__).
func Token(flag string) string {
	if flag != "" {
		return flag
	}
	return os.Getenv("FLYBALL_TOKEN")
}

// tokenDir is where a token flyball login minted lives:
// $XDG_CONFIG_HOME/flyball (os.UserConfigDir on Linux/macOS/Windows
// alike), created on first use. Config, not state: the same directory
// the old session cookie lived in.
func tokenDir() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("finding a config directory: %w", err)
	}
	return filepath.Join(dir, "flyball"), nil
}

// tokenFilePath is the file one front's login token is kept in: one file
// per base URL (a host may run several runners, or the same host
// direct vs. daemon-routed), named from the URL so it's stable and safe
// as a filename.
func tokenFilePath(key string) (string, error) {
	dir, err := tokenDir()
	if err != nil {
		return "", err
	}
	name := strings.NewReplacer(
		"://", "_", "/", "_", ":", "_", "?", "_", "#", "_",
	).Replace(strings.TrimRight(key, "/"))
	return filepath.Join(dir, "token-"+name), nil
}

// SaveToken persists a bearer token secret for key (see loginKey),
// restricted to the owner since it is a credential same as a password
// would be.
func SaveToken(key, secret string) error {
	path, err := tokenFilePath(key)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(secret), 0o600)
}

// LoadToken returns the saved token secret for key, or "" if there isn't
// one (never treated as an error -- callers fall back to
// anonymous/explicit-token access).
func LoadToken(key string) string {
	path, err := tokenFilePath(key)
	if err != nil {
		return ""
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// ClearToken removes the saved token for key, if any.
func ClearToken(key string) error {
	path, err := tokenFilePath(key)
	if err != nil {
		return err
	}
	err = os.Remove(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// loginKey is what a login is filed under: the base URL alone, not the
// daemon-routed prefix -- the runner behind -s NAME is reached at a
// stable address (the daemon's own base URL plus that name) whichever
// way it's addressed, and a front's password is a property of the front,
// not of which daemon happens to be routing to it today. Direct mode
// (t.Prefix == "") already keys on the front's own URL.
func loginKey(t Target) string {
	return strings.TrimRight(t.BaseURL, "/") + t.Prefix
}

// LoggedIn describes the token Login minted.
type LoggedIn struct {
	ID      string
	Name    string
	Scopes  []string
	Kind    string
	Expires time.Time
}

// tokenRow mirrors enough of store.Token's wire shape (front/auth.go's
// createToken) to read POST .../api/auth/tokens' {"token", ...row}
// response without importing the front package's store, which would
// pull the whole front into every CLI build for one response shape.
type tokenRow struct {
	Secret  string    `json:"token"`
	ID      string    `json:"id"`
	Name    string    `json:"name"`
	Scopes  []string  `json:"scopes"`
	Kind    string    `json:"kind"`
	Created time.Time `json:"created"`
	Expires time.Time `json:"expires"`
}

// Login exchanges password for a named token at t (§WP0-8's
// POST .../api/auth/login then POST .../api/auth/tokens, B1's "C2" note):
// first a session cookie, then a token minted with that session and the
// default scope read (auth.md, rv-codebase C8) -- the token, not the
// session, is what's persisted, so a front restart (which drops every
// session) does not silently sign the CLI out. The password itself never
// touches disk or argv; only the minted secret is saved, 0600.
func Login(t Target, password string) (LoggedIn, error) {
	origin := strings.TrimRight(t.BaseURL, "/")
	base := origin + t.Prefix

	loginBody, _ := json.Marshal(map[string]string{"password": password})
	req, err := http.NewRequest("POST", base+"/api/auth/login", bytes.NewReader(loginBody))
	if err != nil {
		return LoggedIn{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", origin) // the front's Origin check (merge requirement 9)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return LoggedIn{}, fmt.Errorf("reaching %s: %w", base, err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return LoggedIn{}, err
	}
	if resp.StatusCode >= 400 {
		return LoggedIn{}, fmt.Errorf("login: %s: %s", resp.Status, strings.TrimSpace(string(data)))
	}
	var cookie *http.Cookie
	for _, c := range resp.Cookies() {
		if c.Value != "" {
			cookie = c
		}
	}
	if cookie == nil {
		return LoggedIn{}, fmt.Errorf("login: no session cookie in the response (this front has no password sign-in)")
	}

	name := loginTokenName()
	tokenBody, _ := json.Marshal(map[string]any{"name": name, "scopes": []string{"read"}})
	req2, err := http.NewRequest("POST", base+"/api/auth/tokens", bytes.NewReader(tokenBody))
	if err != nil {
		return LoggedIn{}, err
	}
	req2.Header.Set("Content-Type", "application/json")
	req2.Header.Set("Origin", origin)
	req2.AddCookie(cookie)
	resp2, err := http.DefaultClient.Do(req2)
	if err != nil {
		return LoggedIn{}, fmt.Errorf("creating a token at %s: %w", base, err)
	}
	defer resp2.Body.Close()
	data2, err := io.ReadAll(resp2.Body)
	if err != nil {
		return LoggedIn{}, err
	}
	if resp2.StatusCode >= 300 {
		return LoggedIn{}, fmt.Errorf("creating a token: %s: %s", resp2.Status, strings.TrimSpace(string(data2)))
	}
	var row tokenRow
	if err := json.Unmarshal(data2, &row); err != nil {
		return LoggedIn{}, fmt.Errorf("creating a token: decoding the response: %w", err)
	}
	if row.Secret == "" {
		return LoggedIn{}, fmt.Errorf("creating a token: the response carried no token")
	}
	if err := SaveToken(loginKey(t), row.Secret); err != nil {
		return LoggedIn{}, err
	}
	return LoggedIn{ID: row.ID, Name: row.Name, Scopes: row.Scopes, Kind: row.Kind, Expires: row.Expires}, nil
}

// loginTokenName names the token flyball login mints, so it's
// recognisable in `flyball token list` and the front's audit.
func loginTokenName() string {
	host, err := os.Hostname()
	if err != nil || host == "" {
		host = "cli"
	}
	return "login@" + host
}

// Logout drops the locally saved token for t. It does not ask the front
// to revoke it: the token routes (DELETE .../api/auth/tokens/{id}) need
// the admin session or the local shape, not a bearer token, so a token
// cannot revoke itself (front/auth.go's tokensRoute) -- `flyball token
// revoke ID` is the way to actually kill a token server-side. This just
// stops this machine from presenting it.
func Logout(t Target) error {
	return ClearToken(loginKey(t))
}

// AuthHeaders returns the headers a request to t should carry: an
// explicit --token/FLYBALL_TOKEN if set, else the token flyball login
// saved for t, else neither (anonymous). An explicit token takes
// precedence deliberately -- stated on the command line, it's the
// caller's choice of identity for this call.
func (t Target) AuthHeaders() http.Header {
	h := http.Header{}
	secret := t.token
	if secret == "" {
		secret = LoadToken(loginKey(t))
	}
	if secret != "" {
		h.Set("Authorization", "Bearer "+secret)
	}
	return h
}
