package exposure

import (
	"strings"
	"testing"
)

func TestIsLoopback(t *testing.T) {
	for addr, want := range map[string]bool{
		"127.0.0.1:8000": true, "localhost:8000": true, "[::1]:8000": true, "127.0.1.1:80": true,
		"127.0.0.1": true, "::1": true, "LOCALHOST": true,
		":8000": false, "0.0.0.0:8000": false, "[::]:8000": false, "192.168.1.3:8000": false,
		"pi.local:8000": false, "": false,
	} {
		if got := IsLoopback(addr); got != want {
			t.Errorf("IsLoopback(%q) = %v, want %v", addr, got, want)
		}
	}
}

func TestWarnings(t *testing.T) {
	if w := OpenWarning(":8000"); !strings.Contains(w, "OPEN") || !strings.Contains(w, "every interface") {
		t.Errorf("OpenWarning = %q", w)
	}
	if w := CleartextWarning("0.0.0.0:8000"); !strings.Contains(w, "unencrypted") {
		t.Errorf("CleartextWarning = %q", w)
	}
}
