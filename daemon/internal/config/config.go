// Package config loads the three config layers from
// brain/plans/rig-deployment/config-layers.md:
//
//	layer 1 -- the daemon's own config (this file's DaemonConfig)
//	layer 2 -- a runner's identity and startup parameters (Manifest)
//	layer 3 -- the runner's own daemon: section -- unchanged, owned by
//	           the Python side (engine/src/flyball/runtime/config.py),
//	           not read or parsed here at all.
package config

import (
	"fmt"
	"net"
	"os"
	"path"
	"path/filepath"
	"regexp"

	"gopkg.in/yaml.v3"
)

// DaemonConfig is layer 1: settings about the daemon itself, never about
// any one runner. See config-layers.md's Layer 1 table.
type DaemonConfig struct {
	Listen        string `yaml:"listen"`         // default 127.0.0.1:9000
	DefaultServer string `yaml:"default_server"` // which identity when -s is omitted
	ManifestsDir  string `yaml:"manifests_dir"`  // where layer-2 files live
	DataDir       string `yaml:"data_dir"`       // registry state, captured logs
	LogMaxSize    int64  `yaml:"log_max_size"`   // per-runner captured-log cap, bytes

	// Auth.Token is the bearer token every mutating route (start, stop,
	// restart, logs) requires, the same shape as a runner's own --token.
	// Empty: those routes answer 503 until one is set.
	//
	// Auth.InsecureOpen lets flyballd, listening beyond loopback, proxy to
	// a runner that has no password and no token. Off: such a runner's
	// routes answer 503 (api.go's guard) -- anyone who reached them could
	// operate its rig.
	Auth struct {
		Token        string `yaml:"token"`
		InsecureOpen bool   `yaml:"insecure_open"`
	} `yaml:"auth"`
}

// DefaultDaemonConfig matches what plan.md's CLI addressing section
// settled on: daemon listens on 9000 (a runner's own default is 8000,
// deliberately distinct, no collision).
func DefaultDaemonConfig() DaemonConfig {
	return DaemonConfig{
		Listen:       "127.0.0.1:9000",
		ManifestsDir: "manifests",
		DataDir:      "data",
		LogMaxSize:   10 << 20, // 10MiB, a placeholder default
	}
}

func LoadDaemonConfig(path string) (DaemonConfig, error) {
	cfg := DefaultDaemonConfig()
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return cfg, nil // no file yet: run on defaults
	}
	if err != nil {
		return cfg, fmt.Errorf("reading daemon config: %w", err)
	}
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return cfg, fmt.Errorf("parsing daemon config %s: %w", path, err)
	}
	return cfg, nil
}

// Manifest is layer 2: a runner's identity and what's needed to start it
// up. Not a daemon-exclusive concept -- the same information CLI flags
// already provide by hand (config-layers.md's Layer 2).
// JSON tags added alongside the existing YAML ones so the same struct
// also decodes POST /api/runners' body correctly (interface.md's table)
// -- encoding/json's tagless case-insensitive matching doesn't bridge
// snake_case wire names like server_config to Go's ServerConfig field.
type Manifest struct {
	Name         string `yaml:"name" json:"name"`
	ServerConfig string `yaml:"server_config" json:"server_config"` // path to the layer-3 daemon: file
	Restart      string `yaml:"restart" json:"restart"`             // always | on-failure | never
	Host         string `yaml:"host" json:"host"`                   // loopback only (default 127.0.0.1): reached through flyballd's proxy
	Port         int    `yaml:"port" json:"port"`
	RootPath     string `yaml:"root_path" json:"root_path"`
	Store        string `yaml:"store" json:"store"`
	Enabled      *bool  `yaml:"enabled" json:"enabled"`
	// UvProject, when set, launches flyball-runner via `uv run --project
	// UvProject flyball-runner ...` instead of execing it bare -- needed
	// whenever flyball-runner isn't already on flyballd's own $PATH, which
	// it never is outside an app's own uv-managed venv (same problem, same
	// fix, as `flyball run`'s --uv flag).
	UvProject string `yaml:"uv_project" json:"uv_project"`
}

func (m Manifest) IsEnabled() bool {
	return m.Enabled == nil || *m.Enabled
}

var (
	namePattern     = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)
	rootPathPattern = regexp.MustCompile(`^(/[a-z0-9][a-z0-9_-]*)+$`)
)

// Validate refuses a manifest whose name or root path could reach outside
// its lane: the name becomes a log file name and a URL prefix, the root
// path a proxy prefix and a line of HTML.
func (m Manifest) Validate() error {
	if !namePattern.MatchString(m.Name) {
		return fmt.Errorf("runner name %q: lower-case letters, digits, - and _ only, up to 64", m.Name)
	}
	if m.ServerConfig == "" {
		return fmt.Errorf("runner %s: server_config is required", m.Name)
	}
	if !isLoopback(m.Host) {
		return fmt.Errorf("runner %s: host %q is not a loopback address: a daemon-supervised runner listens on 127.0.0.1 (the default) or ::1 and is reached through flyballd's proxy under its root_path", m.Name, m.Host)
	}
	if m.Port <= 0 || m.Port > 65535 {
		return fmt.Errorf("runner %s: port %d is not a TCP port", m.Name, m.Port)
	}
	if rp := m.RootPath; !rootPathPattern.MatchString(rp) || path.Clean(rp) != rp {
		return fmt.Errorf("runner %s: root_path %q must be /segments of lower-case letters, digits, - and _, such as /%s", m.Name, rp, m.Name)
	}
	switch m.Restart {
	case "", "always", "on-failure", "never":
	default:
		return fmt.Errorf("runner %s: restart %q: use always, on-failure (the default) or never", m.Name, m.Restart)
	}
	return nil
}

func isLoopback(host string) bool {
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

// LoadManifests reads every *.yaml file in dir as one runner's layer-2
// identity -- one file per runner, per config-layers.md's leaning (not
// finally decided there, but this is what's implemented).
func LoadManifests(dir string) ([]Manifest, error) {
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("reading manifests dir %s: %w", dir, err)
	}
	var manifests []Manifest
	for _, e := range entries {
		if e.IsDir() || filepath.Ext(e.Name()) != ".yaml" {
			continue
		}
		path := filepath.Join(dir, e.Name())
		data, err := os.ReadFile(path)
		if err != nil {
			return nil, fmt.Errorf("reading manifest %s: %w", path, err)
		}
		var m Manifest
		if err := yaml.Unmarshal(data, &m); err != nil {
			return nil, fmt.Errorf("parsing manifest %s: %w", path, err)
		}
		if m.Name == "" {
			return nil, fmt.Errorf("manifest %s has no name", path)
		}
		if m.Host == "" {
			m.Host = "127.0.0.1"
		}
		if m.RootPath == "" {
			m.RootPath = "/" + m.Name
		}
		if m.Restart == "" {
			m.Restart = "on-failure"
		}
		if err := m.Validate(); err != nil {
			return nil, fmt.Errorf("manifest %s: %w", path, err)
		}
		manifests = append(manifests, m)
	}
	return manifests, nil
}
