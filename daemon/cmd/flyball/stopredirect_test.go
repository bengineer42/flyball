package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

// ssoProxy is a front behind an SSO proxy that has no session for the
// CLI: it answers every request but its sign-in page with a 302 there,
// and the sign-in page with 200 HTML. Every request it sees is recorded,
// "METHOD PATH".
type ssoProxy struct {
	mu   sync.Mutex
	seen []string
}

func (p *ssoProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	p.mu.Lock()
	p.seen = append(p.seen, r.Method+" "+r.URL.Path)
	p.mu.Unlock()
	if r.URL.Path == "/oauth2/sign_in" {
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte("<html><body>Sign in with Google</body></html>"))
		return
	}
	http.Redirect(w, r, "/oauth2/sign_in", http.StatusFound)
}

func (p *ssoProxy) requests() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.seen...)
}

// TestStopTreatsARedirectAsARefusal: a 302 from the front (an SSO proxy
// sending the CLI to its sign-in page) is not a stop. It is not
// followed -- following it turns the POST into a GET and reads the
// sign-in page's 200 as success -- and it is reported as a refusal that
// names where it pointed, with a non-zero exit.
func TestStopTreatsARedirectAsARefusal(t *testing.T) {
	p := &ssoProxy{}
	srv := httptest.NewServer(p)
	defer srv.Close()
	t.Setenv("FLYBALL_URL", srv.URL)
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())

	var err error
	out := captureStdout(t, func() { err = runStopCommand("", "", nil) })
	if err == nil {
		t.Fatalf("a redirected stop exited 0 (stdout %q, requests %v)", out, p.requests())
	}
	if !strings.Contains(err.Error(), "/oauth2/sign_in") {
		t.Errorf("error %q does not name the redirect's Location", err)
	}
	if got := p.requests(); len(got) != 1 || got[0] != "POST /api/rig/stop" {
		t.Errorf("requests = %v, want only the POST (the redirect is not followed)", got)
	}
	if strings.Contains(out, "Sign in") {
		t.Errorf("stdout %q printed the sign-in page as a stop report", out)
	}
}

// TestStopRequiresAStopReport: a 200 whose body is not a stop report (an
// HTML page from whatever sits in front) is not a stop either.
func TestStopRequiresAStopReport(t *testing.T) {
	for name, body := range map[string]string{
		"html":        "<html><body>Welcome</body></html>",
		"empty":       "",
		"json object": `{"ok": true}`,
	} {
		t.Run(name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Write([]byte(body))
			}))
			defer srv.Close()
			t.Setenv("FLYBALL_URL", srv.URL)
			t.Setenv("XDG_CONFIG_HOME", t.TempDir())
			var err error
			captureStdout(t, func() { err = runStopCommand("", "", nil) })
			if err == nil {
				t.Fatalf("a 200 with body %q counted as a stop", body)
			}
		})
	}
}

// TestStopAllTreatsRedirectsAsFailures: `stop --all` behind the same
// proxy. A redirected rig list stops nothing and says where it pointed;
// a rig whose stop is redirected counts as failed.
func TestStopAllTreatsRedirectsAsFailures(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Run("list", func(t *testing.T) {
		p := &ssoProxy{}
		srv := httptest.NewServer(p)
		defer srv.Close()
		t.Setenv("FLYBALLD_URL", srv.URL)
		var err error
		captureStdout(t, func() { err = runStopCommand("", "", []string{"--all"}) })
		if err == nil || !strings.Contains(err.Error(), "/oauth2/sign_in") {
			t.Fatalf("err = %v, want a refusal naming the redirect", err)
		}
		if got := p.requests(); len(got) != 1 || got[0] != "GET /api/rigs" {
			t.Errorf("requests = %v, want only the list (the redirect is not followed)", got)
		}
	})
	t.Run("per rig", func(t *testing.T) {
		p := &ssoProxy{}
		mux := http.NewServeMux()
		mux.HandleFunc("GET /api/rigs", func(w http.ResponseWriter, r *http.Request) {
			json.NewEncoder(w).Encode([]map[string]string{{"name": "a", "root_path": "/a"}})
		})
		mux.Handle("/", p)
		srv := httptest.NewServer(mux)
		defer srv.Close()
		t.Setenv("FLYBALLD_URL", srv.URL)
		var err error
		out := captureStdout(t, func() { err = runStopCommand("", "", []string{"--all"}) })
		if err == nil {
			t.Fatalf("a redirected per-rig stop exited 0 (stdout %q)", out)
		}
		if !strings.Contains(out, "/oauth2/sign_in") {
			t.Errorf("stdout %q does not name the redirect for rig a", out)
		}
		if got := p.requests(); len(got) != 1 || got[0] != "POST /a/api/rig/stop" {
			t.Errorf("requests = %v, want only the POST", got)
		}
	})
}
