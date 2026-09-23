package exposure

import (
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
