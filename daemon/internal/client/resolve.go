// Package client resolves which base URL a CLI invocation should hit,
// and is shared between the daemon (which doesn't need it) and the
// flyball CLI (cmd/flyball) -- living here so both binaries in this
// module can use the same resolution and wire-request logic without
// duplicating it.
package client

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
)

const (
	DefaultRunnerURL = "http://127.0.0.1:8000" // FLYBALL_URL default
	DefaultDaemonURL = "http://127.0.0.1:9000" // FLYBALLD_URL default
)

// Target is the resolved base URL and path prefix for one CLI call.
type Target struct {
	BaseURL string
	Prefix  string // "" direct-to-runner; "/NAME" when daemon-routed
	token   string // bearer token, if any -- set via WithToken
}

// WithToken returns a copy of t that sends token as a bearer token on
// every request (see auth.go's AuthHeaders), taking precedence over any
// saved session cookie.
func (t Target) WithToken(token string) Target {
	t.token = token
	return t
}

// Resolve implements this precedence exactly:
//   - server == "": FLYBALL_URL alone, direct-to-runner, daemon not
//     involved at all -- unchanged from today's CLI.
//   - server != "": FLYBALLD_URL (the daemon), path prefixed with
//     "/"+server -- reuses the daemon's routing, no separate resolution
//     call needed.
func Resolve(server string) (Target, error) {
	if server == "" {
		url := os.Getenv("FLYBALL_URL")
		if url == "" {
			url = DefaultRunnerURL
		}
		return Target{BaseURL: url}, nil
	}
	url := os.Getenv("FLYBALLD_URL")
	if url == "" {
		url = DefaultDaemonURL
	}
	return Target{BaseURL: url, Prefix: "/" + server}, nil
}

// ResolveDefault implements the default-runner precedence when -s is
// omitted but FLYBALLD_URL / --daemon points at a daemon: exactly one
// runner registered, or one marked default (layer 1's default_server),
// else error listing the names.
func ResolveDefault(daemonURL string) (Target, error) {
	resp, err := http.Get(strings.TrimRight(daemonURL, "/") + "/api/runners")
	if err != nil {
		return Target{}, fmt.Errorf("reaching daemon at %s: %w", daemonURL, err)
	}
	defer resp.Body.Close()

	var runners []struct {
		Name string `json:"name"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&runners); err != nil {
		return Target{}, fmt.Errorf("decoding runner list: %w", err)
	}

	switch len(runners) {
	case 0:
		return Target{}, fmt.Errorf("no runners registered with this daemon")
	case 1:
		return Target{BaseURL: daemonURL, Prefix: "/" + runners[0].Name}, nil
	default:
		names := make([]string, len(runners))
		for i, r := range runners {
			names[i] = r.Name
		}
		// TODO: check layer 1's default_server before erroring, once the
		// daemon's /api exposes it (not yet).
		return Target{}, fmt.Errorf(
			"more than one runner registered (%s) -- pass -s/--server",
			strings.Join(names, ", "),
		)
	}
}

// Raw sends a request relative to t and returns the raw response body,
// for endpoints that don't return JSON (e.g. history export's csv/zip).
func (t Target) Raw(method, path string, body io.Reader) ([]byte, error) {
	req, err := http.NewRequest(method, strings.TrimRight(t.BaseURL, "/")+t.Prefix+path, body)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for name, values := range t.AuthHeaders() {
		for _, v := range values {
			req.Header.Add(name, v)
		}
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		return nil, authAwareError(method, path, resp, data)
	}
	return data, nil
}

// Do sends a request relative to t and decodes a JSON response.
func (t Target) Do(method, path string, body io.Reader, out any) error {
	req, err := http.NewRequest(method, strings.TrimRight(t.BaseURL, "/")+t.Prefix+path, body)
	if err != nil {
		return err
	}
	if body != nil {
		// Required, not cosmetic: without it, the runner's FastAPI layer
		// parses a JSON-object body as a Python string rather than a
		// dict (confirmed live -- a raw curl POST without this header
		// gets the same "Input should be a valid dictionary" error the
		// Go client hit before this was added).
		req.Header.Set("Content-Type", "application/json")
	}
	for name, values := range t.AuthHeaders() {
		for _, v := range values {
			req.Header.Add(name, v)
		}
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		data, _ := io.ReadAll(resp.Body)
		return authAwareError(method, path, resp, data)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// authAwareError wraps a >=400 response, adding a hint for 401s since
// "unauthorized" alone doesn't tell the caller what to do about it --
// matching the runner's own WWW-Authenticate: Bearer convention
// (auth.py's Auth.__call__).
func authAwareError(method, path string, resp *http.Response, data []byte) error {
	if resp.StatusCode == http.StatusUnauthorized {
		return fmt.Errorf(
			"%s %s: %s: %s (sign in with `flyball login`, or set FLYBALL_TOKEN/--token)",
			method, path, resp.Status, strings.TrimSpace(string(data)),
		)
	}
	return fmt.Errorf("%s %s: %s: %s", method, path, resp.Status, string(data))
}
