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
