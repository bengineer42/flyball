package client

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// flyballd's runner list needs its token: ResolveDefault sends
// FLYBALLD_TOKEN like every other daemon request.
func TestResolveDefaultSendsTheDaemonToken(t *testing.T) {
	daemon := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer s3cret" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		w.Write([]byte(`[{"name":"oven"}]`))
	}))
	defer daemon.Close()
	t.Setenv("FLYBALLD_TOKEN", "s3cret")

	target, err := ResolveDefault(daemon.URL)
	if err != nil {
		t.Fatal(err)
	}
	if target.Prefix != "/oven" {
		t.Errorf("prefix %q, want /oven", target.Prefix)
	}
}

func TestResolveDefaultReportsARefusal(t *testing.T) {
	daemon := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
	}))
	defer daemon.Close()
	t.Setenv("FLYBALLD_TOKEN", "")

	if _, err := ResolveDefault(daemon.URL); err == nil {
		t.Error("a 401 from the daemon was not an error")
	}
}
