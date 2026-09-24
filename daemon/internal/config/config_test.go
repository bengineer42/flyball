package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"flyballd/internal/endpoint"
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

// setGOOS sets the platform D-044's tcp rule is decided for, for one test.
func setGOOS(t *testing.T, goos string) {
	t.Helper()
	was := endpoint.GOOS
	endpoint.GOOS = goos
	t.Cleanup(func() { endpoint.GOOS = was })
}

// A runner fronted over a unix socket needs no port; one on TCP, which is
// Windows only (D-044), does.
func TestManifestPortOptional(t *testing.T) {
	setGOOS(t, "linux")
	m := valid()
	m.Port = 0
	for _, n := range []string{"", "unix"} {
		m.Network = n
		if err := m.Validate(); err != nil {
			t.Errorf("network %q without a port: %v", n, err)
		}
		if got := m.ResolvedNetwork(); got != "unix" {
			t.Errorf("network %q resolves to %q", n, got)
		}
	}

	setGOOS(t, "windows")
	m.Network = ""
	if got := m.ResolvedNetwork(); got != "tcp" {
		t.Errorf("windows: the default network resolves to %q", got)
	}
	m.Network = "unix"
	if err := m.Validate(); err == nil || !strings.Contains(err.Error(), "use tcp") {
		t.Errorf("windows: network unix accepted (%v)", err)
	}
	for _, n := range []string{"", "tcp"} {
		m.Network, m.Port = n, 0
		if err := m.Validate(); err == nil || !strings.Contains(err.Error(), "port") {
			t.Errorf("windows: network %q without a port accepted (%v)", n, err)
		}
		m.Port = 8101
		if err := m.Validate(); err != nil {
			t.Errorf("windows: network %q with a port: %v", n, err)
		}
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

// Off Windows a manifest's network: tcp still loads, port or no port:
// the backend runs that runner on the unix socket in its front-dir and
// logs why (D-044, D-028: a misconfiguration removes exposure, never
// operation). The manifest keeps what was asked.
func TestManifestTCPOffWindowsLoads(t *testing.T) {
	for _, goos := range []string{"linux", "darwin"} {
		setGOOS(t, goos)
		for _, port := range []int{0, 8101} {
			m := valid()
			m.Network, m.Port = "tcp", port
			if err := m.Validate(); err != nil {
				t.Errorf("%s: network tcp, port %d: %v", goos, port, err)
			}
			if got := m.ResolvedNetwork(); got != "tcp" {
				t.Errorf("%s: network tcp resolves to %q", goos, got)
			}
		}
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
	setGOOS(t, "windows")
	if _, err := LoadManifests(dir); err == nil || !strings.Contains(err.Error(), "bad.yaml") {
		t.Errorf("windows: a tcp manifest with no port loaded (%v)", err)
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
