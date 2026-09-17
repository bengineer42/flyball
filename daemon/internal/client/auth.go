// Auth support: bearer tokens and session cookies for talking to a
// password/token-protected runner (engine/src/flyball/server/auth.py).
//
// Two mechanisms, matching the runner's own (see auth.py's docstring):
//   - a bearer token, sent as `Authorization: Bearer T` -- the machine
//     case, e.g. FLYBALL_TOKEN or --token, unchanged shape from the old
//     Python client/CLI (flyball.client.rig.Rig).
//   - a session cookie, minted by POST /api/auth/login from a password
//     and persisted to a file so later CLI invocations reuse it without
//     asking again -- new: the old cli.py never had a login flow, only
//     the bearer token.
//
// Both are carried on client.Target so Do/Raw send them on every
// request, and are exposed for wsclient.Dial's headers too (watch).
package client

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// Token is the bearer token to send, if any: --token wins over
// FLYBALL_TOKEN, matching the old Python client's precedence
// (flyball.client.rig.Rig.__init__).
func Token(flag string) string {
	if flag != "" {
		return flag
	}
	return os.Getenv("FLYBALL_TOKEN")
}

// sessionDir is where session cookies live: $XDG_CONFIG_HOME/flyball
// (os.UserConfigDir on Linux/macOS/Windows alike), created on first use.
func sessionDir() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("finding a config directory: %w", err)
	}
	return filepath.Join(dir, "flyball"), nil
}

// sessionPath is the file one runner's session cookie is kept in: one
// file per base URL (a host may run several runners, or the same host
// direct vs. daemon-routed), named from the URL so it's stable and safe
// as a filename.
func sessionPath(baseURL string) (string, error) {
	dir, err := sessionDir()
	if err != nil {
		return "", err
	}
	name := strings.NewReplacer(
		"://", "_", "/", "_", ":", "_", "?", "_", "#", "_",
	).Replace(strings.TrimRight(baseURL, "/"))
	return filepath.Join(dir, "session-"+name), nil
}

// SaveSession persists a session cookie value for baseURL, restricted to
// the owner since it's a bearer credential same as a password would be.
func SaveSession(baseURL, cookie string) error {
	path, err := sessionPath(baseURL)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(cookie), 0o600)
}

// LoadSession returns the saved session cookie for baseURL, or "" if
// there isn't one (never treated as an error -- callers fall back to
// anonymous/token access).
func LoadSession(baseURL string) string {
	path, err := sessionPath(baseURL)
	if err != nil {
		return ""
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

// ClearSession removes the saved session cookie for baseURL, if any.
func ClearSession(baseURL string) error {
	path, err := sessionPath(baseURL)
	if err != nil {
		return err
	}
	err = os.Remove(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

// authOut mirrors the runner's AuthOut (routes/auth.py): enough to tell
// login apart from a wrong password vs. an open runner.
type authOut struct {
	Scheme    string `json:"scheme"`
	Level     string `json:"level"`
	Anonymous string `json:"anonymous"`
	Password  bool   `json:"password"`
	Token     bool   `json:"token"`
}

// Login posts secret (a password, or a token typed at the prompt -- the
// runner's /api/auth/login takes either, per auth.py's Auth.is_secret)
// to baseURL+prefix+"/api/auth/login" and, on success, saves the session
// cookie the runner sets, keyed to baseURL (not baseURL+prefix: the
// runner's session is the same whichever path prefix it's reached
// through in daemon-routed mode -- but see note in login.go about using
// the resolved target's full address as the key instead).
func Login(t Target, secret string) error {
	body, err := json.Marshal(map[string]string{"secret": secret})
	if err != nil {
		return err
	}
	url := strings.TrimRight(t.BaseURL, "/") + t.Prefix + "/api/auth/login"
	req, err := http.NewRequest("POST", url, strings.NewReader(string(body)))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode >= 400 {
		return fmt.Errorf("login: %s: %s", resp.Status, strings.TrimSpace(string(data)))
	}
	var cookie string
	for _, c := range resp.Cookies() {
		if c.Name == "flyball_session" {
			cookie = c.Value
		}
	}
	var out authOut
	_ = json.Unmarshal(data, &out)
	if cookie == "" {
		if out.Level != "operate" {
			return fmt.Errorf("login: no session cookie returned and level is %q", out.Level)
		}
		// A runner with no auth configured at all (auth==None) answers
		// 200 with no cookie -- nothing to save, login is moot.
		return nil
	}
	return SaveSession(sessionKey(t), cookie)
}

// Logout clears the saved session for t, best-effort telling the runner
// too (so the cookie is invalidated server-side where that matters --
// today the runner's sessions are stateless HMACs with no revocation
// list, so this is mostly for a shared machine's /api/auth/logout audit
// trail, not required for the client-side clear to be effective).
func Logout(t Target) error {
	url := strings.TrimRight(t.BaseURL, "/") + t.Prefix + "/api/auth/logout"
	if req, err := http.NewRequest("POST", url, nil); err == nil {
		if cookie := LoadSession(sessionKey(t)); cookie != "" {
			req.AddCookie(&http.Cookie{Name: "flyball_session", Value: cookie})
		}
		if resp, err := http.DefaultClient.Do(req); err == nil {
			resp.Body.Close()
		}
	}
	return ClearSession(sessionKey(t))
}

// sessionKey is what a session is filed under: the base URL alone, not
// the daemon-routed prefix -- the runner behind -s NAME is reached at a
// stable address (the daemon's own base URL plus that name) whichever
// way it's addressed, and a runner's password is a property of the
// runner, not of which daemon happens to be routing to it today. Direct
// mode (t.Prefix == "") already keys on the runner's own URL.
func sessionKey(t Target) string {
	return strings.TrimRight(t.BaseURL, "/") + t.Prefix
}

// AuthHeaders returns the headers a request to t should carry: bearer
// token if set, else the saved session cookie if there is one, else
// neither (anonymous). Token takes precedence over a stale session
// cookie deliberately -- an explicit --token/FLYBALL_TOKEN on the
// command line is the caller stating how they want to authenticate.
func (t Target) AuthHeaders() http.Header {
	h := http.Header{}
	if t.token != "" {
		h.Set("Authorization", "Bearer "+t.token)
		return h
	}
	if cookie := LoadSession(sessionKey(t)); cookie != "" {
		h.Set("Cookie", "flyball_session="+cookie)
	}
	return h
}
