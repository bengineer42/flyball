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
	"os"
	"path/filepath"

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

	Auth struct {
		Password string `yaml:"password"`
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
	Host         string `yaml:"host" json:"host"`                   // always 127.0.0.1 when daemon-supervised
	Port         int    `yaml:"port" json:"port"`
	RootPath     string `yaml:"root_path" json:"root_path"`
	Store        string `yaml:"store" json:"store"`
	Enabled      *bool  `yaml:"enabled" json:"enabled"`
}

func (m Manifest) IsEnabled() bool {
	return m.Enabled == nil || *m.Enabled
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
		manifests = append(manifests, m)
	}
	return manifests, nil
}
