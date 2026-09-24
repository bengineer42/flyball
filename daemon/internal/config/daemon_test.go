package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func loadDaemon(t *testing.T, yaml string) (DaemonConfig, error) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "flyballd.yaml")
	if err := os.WriteFile(path, []byte(yaml), 0o600); err != nil {
		t.Fatal(err)
	}
	return LoadDaemonConfig(path)
}

// The front's keys sit at the top level of flyballd.yaml, beside the
// daemon's own.
func TestDaemonConfigFrontKeysAtTheTop(t *testing.T) {
	cfg, err := loadDaemon(t, "listen: 0.0.0.0:9443\nauth: password\npassword: $scrypt$x\nanonymous: read\n"+
		"url: https://pi.lab:9443\nmanifests_dir: m\ndata_dir: d\n")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Front.Listen != "0.0.0.0:9443" || cfg.Front.Auth != "password" || cfg.Front.Anonymous != "read" ||
		cfg.Front.URL != "https://pi.lab:9443" || filepath.Base(cfg.ManifestsDir) != "m" || filepath.Base(cfg.DataDir) != "d" ||
		cfg.FrontError != nil {
		t.Fatalf("cfg = %+v", cfg)
	}
}

// With no manifests_dir or data_dir: systemd's StateDirectory=
// ($STATE_DIRECTORY, the first of several), else /var/lib/flyball.
func TestDaemonConfigDefaults(t *testing.T) {
	t.Setenv("STATE_DIRECTORY", "")
	cfg, err := LoadDaemonConfig(filepath.Join(t.TempDir(), "absent.yaml"))
	if err != nil || cfg.Front.Listen != "127.0.0.1:9000" || cfg.ManifestsDir != "/var/lib/flyball/manifests" ||
		cfg.DataDir != "/var/lib/flyball" {
		t.Fatalf("cfg = %+v, %v", cfg, err)
	}
	t.Setenv("STATE_DIRECTORY", "/srv/state:/srv/other")
	cfg, err = loadDaemon(t, "listen: 127.0.0.1:9001\n")
	if err != nil || cfg.ManifestsDir != "/srv/state/manifests" || cfg.DataDir != "/srv/state" {
		t.Fatalf("cfg = %+v, %v", cfg, err)
	}
	cfg, err = loadDaemon(t, "data_dir: /abs/d\n")
	if err != nil || cfg.Front.Listen != "127.0.0.1:9000" || cfg.DataDir != "/abs/d" || cfg.ManifestsDir != "/srv/state/manifests" {
		t.Fatalf("cfg = %+v, %v", cfg, err)
	}
}

// A relative manifests_dir or data_dir is under flyballd.yaml's directory,
// not the process's cwd -- also when the config's own path is relative.
func TestDaemonConfigRelativeDirsUnderTheFile(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "flyballd.yaml"), []byte("manifests_dir: m\ndata_dir: ../d\n"), 0o600)
	t.Chdir(t.TempDir())
	cfg, err := LoadDaemonConfig(filepath.Join(dir, "flyballd.yaml"))
	if err != nil || cfg.ManifestsDir != filepath.Join(dir, "m") || cfg.DataDir != filepath.Join(filepath.Dir(dir), "d") {
		t.Fatalf("cfg = %+v, %v", cfg, err)
	}
	t.Chdir(filepath.Dir(dir))
	cfg, err = LoadDaemonConfig(filepath.Join(filepath.Base(dir), "flyballd.yaml"))
	if err != nil || cfg.ManifestsDir != filepath.Join(dir, "m") {
		t.Fatalf("cfg = %+v, %v", cfg, err)
	}
}

// The old auth: {token, insecure_open} is gone; a file that still has it
// loads, and the front falls back (D-028) saying what replaced it.
func TestDaemonConfigOldAuthBlock(t *testing.T) {
	cfg, err := loadDaemon(t, "listen: 0.0.0.0:9000\nmanifests_dir: m\nauth:\n  token: s3cret\n  insecure_open: true\n")
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(cfg.ManifestsDir) != "m" || cfg.FrontError == nil || cfg.Front.Listen != "0.0.0.0:9000" {
		t.Fatalf("cfg = %+v", cfg)
	}
	msg := cfg.FrontError.Error()
	if !strings.Contains(msg, "flyball token create") || !strings.Contains(msg, "--insecure-open") || strings.Contains(msg, "s3cret") {
		t.Fatalf("FrontError = %q", msg)
	}
}

// A front key that cannot be read (wrong type, unknown key) is the
// front's fallback, not the daemon's failure; a bad daemon key is.
func TestDaemonConfigBadKeys(t *testing.T) {
	for _, y := range []string{"auth: [local]\n", "lisen: 1\n", "tls: {cert: /c, key: /k, chain: /x}\n"} {
		cfg, err := loadDaemon(t, "manifests_dir: m\n"+y)
		if err != nil || cfg.FrontError == nil || filepath.Base(cfg.ManifestsDir) != "m" {
			t.Fatalf("%q: cfg = %+v, %v", y, cfg, err)
		}
	}
	if _, err := loadDaemon(t, "log_max_size: lots\n"); err == nil {
		t.Fatal("a bad daemon key must be an error")
	}
}
