package config

import (
	"os"
	"path/filepath"
	"testing"
)

// TestTopLevelTokensBlockReachesFrontConfig: flyballd.yaml's top-level
// `tokens:` is one of the front's own keys (daemonKeys does not list it),
// so it survives to `top` and frontwire.Decode reads it straight into
// front.Config.Tokens -- no daemon-specific handling needed here.
func TestTopLevelTokensBlockReachesFrontConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "flyballd.yaml")
	os.WriteFile(path, []byte("manifests_dir: manifests\ntokens:\n  default_lifetime: 30d\n  max_lifetime: 60d\n"), 0o644)
	cfg, err := LoadDaemonConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.FrontError != nil {
		t.Fatalf("FrontError = %v, want nil", cfg.FrontError)
	}
	if cfg.Front.Tokens == nil || cfg.Front.Tokens.DefaultLifetime != "30d" || cfg.Front.Tokens.MaxLifetime != "60d" {
		t.Fatalf("Front.Tokens = %+v", cfg.Front.Tokens)
	}
}
