package exposure

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
)

// The same cases as engine/tests/test_door.py's, for the runner's rule.
func TestLoopbackName(t *testing.T) {
	for host, want := range map[string]bool{
		"localhost": true, "localhost:8000": true, "LocalHost:8000": true, "127.0.0.1:1": true,
		"[::1]": true, "[::1]:8000": true,
		"localhost.evil.example": false, "127.0.0.1.nip.io": false, "[::2]:8000": false, "": false,
		"127.0.0.2": false, "evil.example": false, "192.168.1.3:8000": false, "localhost:x": false,
	} {
		if got := LoopbackName(host); got != want {
			t.Errorf("LoopbackName(%q) = %v, want %v", host, got, want)
		}
	}
}

func TestSameSite(t *testing.T) {
	for _, c := range []struct {
		origin, host, scheme string
		want                 bool
	}{
		{"http://localhost:8000", "localhost:8000", "http", true},
		{"http://LOCALHOST:8000", "localhost:8000", "http", true},
		{"https://localhost:8000", "localhost:8000", "http", true}, // a TLS proxy in front
		{"http://pi.lab", "pi.lab:80", "http", true},
		{"https://pi.lab", "pi.lab:443", "http", true},
		{"http://pi.lab", "pi.lab:443", "http", false},
		{"http://localhost:8443", "localhost:8443", "https", false}, // downgraded
		{"http://localhost:9999", "localhost:8000", "http", false},
		{"http://evil.example", "localhost:8000", "http", false},
		{"null", "localhost:8000", "http", false},
		{"", "localhost:8000", "http", false},
		{"ftp://localhost:8000", "localhost:8000", "http", false},
	} {
		if got := SameSite(c.origin, c.host, c.scheme); got != c.want {
			t.Errorf("SameSite(%q, %q, %q) = %v, want %v", c.origin, c.host, c.scheme, got, c.want)
		}
	}
}

func outgoing(host, origin string) *http.Request {
	r := httptest.NewRequest("POST", "/api/runner/shutdown", nil)
	r.Host = host
	if origin != "" {
		r.Header.Set("Origin", origin)
	}
	return r
}

func TestTranslate(t *testing.T) {
	upstream, _ := url.Parse("http://127.0.0.1:18356")
	open := func(context.Context) (Door, error) { return Door{}, nil }
	shut := func(context.Context) (Door, error) { return Door{Password: true}, nil }
	unknown := func(context.Context) (Door, error) { return Door{}, errors.New("no answer") }
	for _, c := range []struct {
		name         string
		openNetwork  bool
		door         func(context.Context) (Door, error)
		host, origin string
		wantHost     string
		wantOrigin   string
	}{
		{"loopback front, own page", false, open, "localhost:18357", "http://localhost:18357", "127.0.0.1:18356", "http://127.0.0.1:18356"},
		{"loopback front, no origin", false, open, "127.0.0.1:18357", "", "127.0.0.1:18356", ""},
		{"loopback front, rebound name", false, open, "evil.example", "http://evil.example", "evil.example", "http://evil.example"},
		{"loopback front, LAN name", false, open, "192.168.1.3:18357", "http://192.168.1.3:18357", "192.168.1.3:18357", "http://192.168.1.3:18357"},
		{"opted in, own page", true, open, "192.168.1.3:18357", "http://192.168.1.3:18357", "127.0.0.1:18356", "http://127.0.0.1:18356"},
		{"opted in, TLS proxy's page", true, open, "pi.lab", "https://pi.lab", "127.0.0.1:18356", "http://127.0.0.1:18356"},
		{"opted in, another site", true, open, "192.168.1.3:18357", "http://evil.example", "127.0.0.1:18356", "http://evil.example"},
		{"opted in, null", true, open, "192.168.1.3:18357", "null", "127.0.0.1:18356", "null"},
		{"opted in, the runner's own origin from elsewhere", true, open, "192.168.1.3:18357", "http://127.0.0.1:18356", "127.0.0.1:18356", "null"},
		{"door unknown counts as open", true, unknown, "192.168.1.3:18357", "http://192.168.1.3:18357", "127.0.0.1:18356", "http://127.0.0.1:18356"},
		{"password runner, loopback", false, shut, "localhost:18357", "http://localhost:18357", "localhost:18357", "http://localhost:18357"},
		{"password runner, opted in", true, shut, "192.168.1.3:18357", "http://192.168.1.3:18357", "192.168.1.3:18357", "http://192.168.1.3:18357"},
	} {
		t.Run(c.name, func(t *testing.T) {
			f := &Front{Upstream: upstream, OpenNetwork: c.openNetwork, Door: c.door}
			r := outgoing(c.host, c.origin)
			f.Translate(r)
			if r.Host != c.wantHost || r.Header.Get("Origin") != c.wantOrigin {
				t.Errorf("Host %q Origin %q, want %q %q", r.Host, r.Header.Get("Origin"), c.wantHost, c.wantOrigin)
			}
		})
	}
}

func TestDoorsRemembers(t *testing.T) {
	asked := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		asked++
		w.Write([]byte(`{"password": true, "token": false}`))
	}))
	defer srv.Close()
	d := NewDoors()
	for i := 0; i < 3; i++ {
		door, err := d.Get(context.Background(), srv.URL+"/api/auth")
		if err != nil || door.Open() {
			t.Fatalf("Get = %+v, %v", door, err)
		}
	}
	if asked != 1 {
		t.Errorf("asked %d times, want 1 within the TTL", asked)
	}
}
