package config

import (
	"strings"
	"testing"
)

func valid() Manifest {
	return Manifest{Name: "oven", ServerConfig: "oven.yaml", Port: 8101, Host: "127.0.0.1", RootPath: "/oven"}
}

func TestRestartPolicyIsChecked(t *testing.T) {
	for _, policy := range []string{"", "always", "on-failure", "never"} {
		m := valid()
		m.Restart = policy
		if err := m.Validate(); err != nil {
			t.Errorf("restart %q: %v", policy, err)
		}
	}
	m := valid()
	m.Restart = "sometimes"
	if err := m.Validate(); err == nil || !strings.Contains(err.Error(), "restart") {
		t.Errorf("restart: sometimes accepted (%v)", err)
	}
}

// A daemon-supervised runner is reached through flyballd's proxy; it
// never listens beyond loopback itself.
func TestHostMustBeLoopback(t *testing.T) {
	for _, host := range []string{"127.0.0.1", "127.0.0.2", "::1", "localhost"} {
		m := valid()
		m.Host = host
		if err := m.Validate(); err != nil {
			t.Errorf("host %q: %v", host, err)
		}
	}
	for _, host := range []string{"", "0.0.0.0", "::", "192.168.1.3", "example.com"} {
		m := valid()
		m.Host = host
		if err := m.Validate(); err == nil || !strings.Contains(err.Error(), "loopback") {
			t.Errorf("host %q accepted (%v)", host, err)
		}
	}
}
