package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
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

// A runner fronted over a unix socket needs no port; one on TCP does.
func TestManifestPortOptional(t *testing.T) {
	m := valid()
	m.Port = 0
	for _, n := range []string{"", "unix"} {
		m.Network = n
		if runtime.GOOS == "windows" {
			break
		}
		if err := m.Validate(); err != nil {
			t.Errorf("network %q without a port: %v", n, err)
		}
		if got := m.ResolvedNetwork(); got != "unix" {
			t.Errorf("network %q resolves to %q", n, got)
		}
	}
	m.Network = "tcp"
	if err := m.Validate(); err == nil || !strings.Contains(err.Error(), "port") {
		t.Errorf("tcp without a port accepted (%v)", err)
	}
	m.Port = 8101
	if err := m.Validate(); err != nil {
		t.Errorf("tcp with a port: %v", err)
	}
	for _, p := range []int{-1, 65536} {
		m := valid()
		m.Port = p
		if err := m.Validate(); err == nil {
			t.Errorf("port %d accepted", p)
		}
	}
	m = valid()
	m.Network = "udp"
	if err := m.Validate(); err == nil || !strings.Contains(err.Error(), "network") {
		t.Errorf("network udp accepted (%v)", err)
	}
}

func TestManifestAnonymousIsRemoved(t *testing.T) {
	// A manifest's anonymous: was checked and then read by nothing: the front's own
	// anonymous: (flyballd.yaml) applies to every rig. Set, it is refused and says where to go.
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "oven.yaml"), []byte("name: oven\nserver_config: oven.yaml\nanonymous: read\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := LoadManifests(dir)
	if err == nil || !strings.Contains(err.Error(), "anonymous") || !strings.Contains(err.Error(), "flyballd.yaml") {
		t.Errorf("a manifest with anonymous: loaded (%v)", err)
	}
	var m Manifest
	if err := json.Unmarshal([]byte(`{"name":"oven","server_config":"o.yaml","anonymous":"none"}`), &m); err != nil {
		t.Fatal(err)
	}
	if err := m.Validate(); err == nil || !strings.Contains(err.Error(), "flyballd.yaml") {
		t.Errorf("POST /api/runners with anonymous accepted (%v)", err)
	}
	out, err := json.Marshal(valid())
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(out), "anonymous") {
		t.Errorf("a manifest without the key still writes it: %s", out)
	}
}

func TestLoadManifestsReadsNetwork(t *testing.T) {
	dir := t.TempDir()
	write := func(name, body string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write("oven.yaml", "name: oven\nserver_config: oven.yaml\n")
	write("kiln.yaml", "name: kiln\nserver_config: kiln.yaml\nnetwork: tcp\nport: 8102\n")
	ms, err := LoadManifests(dir)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]Manifest{}
	for _, m := range ms {
		got[m.Name] = m
	}
	if o := got["oven"]; o.Port != 0 || o.RootPath != "/oven" || o.Host != "127.0.0.1" {
		t.Errorf("oven: %+v", o)
	}
	if k := got["kiln"]; k.Network != "tcp" || k.Port != 8102 || k.ResolvedNetwork() != "tcp" {
		t.Errorf("kiln: %+v", k)
	}

	write("bad.yaml", "name: bad\nserver_config: bad.yaml\nnetwork: tcp\n")
	if _, err := LoadManifests(dir); err == nil || !strings.Contains(err.Error(), "bad.yaml") {
		t.Errorf("a tcp manifest with no port loaded (%v)", err)
	}
}

func TestManifestJSONKeys(t *testing.T) {
	var m Manifest
	if err := json.Unmarshal([]byte(`{"name":"oven","server_config":"o.yaml","network":"tcp","port":8101}`), &m); err != nil {
		t.Fatal(err)
	}
	if m.Network != "tcp" || m.Port != 8101 {
		t.Errorf("%+v", m)
	}
}
