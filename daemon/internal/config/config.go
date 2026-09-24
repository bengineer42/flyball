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
	"errors"
	"fmt"
	"net"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"

	"flyballd/internal/endpoint"
	"flyballd/internal/front"
	"flyballd/internal/frontwire"
	"flyballd/internal/names"

	"gopkg.in/yaml.v3"
)

// DaemonConfig is layer 1: settings about the daemon itself, never about
// any one runner. See config-layers.md's Layer 1 table.
//
// The front's keys (listen, auth, url, tls, password, anonymous, proxy,
// session, trusted_proxies: front.Config, the same block as a rig file's
// runner.front) sit at the top level beside the daemon's own. Management
// (listing, starting, stopping, restarting runners, their logs) needs a
// bearer token with the management scope, made by `flyball token create`;
// --insecure-open is a flag of the run, never a file key.
type DaemonConfig struct {
	Front front.Config `yaml:"-"`
	// FrontError is why the front's keys could not be read; the front
	// then serves the local shape on loopback with a banner (D-028), and
	// the daemon runs.
	FrontError error `yaml:"-"`

	DefaultServer string `yaml:"default_server"` // which identity when -s is omitted
	// ManifestsDir and DataDir are absolute once loaded: by default under
	// StateDir(), and a relative one in the file is under the file's own
	// directory -- never the process's cwd, which a service manager sets
	// to / or leaves wherever flyballd was started.
	ManifestsDir string `yaml:"manifests_dir"` // where layer-2 files live
	DataDir      string `yaml:"data_dir"`      // registry state, captured logs, the front's tokens and audit
	LogMaxSize   int64  `yaml:"log_max_size"`  // per-runner captured-log cap, bytes
}

// DefaultListen is where flyballd's front listens when flyballd.yaml says
// nowhere: 9000 (a flyball run front's default is 8000, deliberately
// distinct).
const DefaultListen = "127.0.0.1:9000"

// DefaultStateDir is flyballd's state directory when systemd names none.
const DefaultStateDir = "/var/lib/flyball"

// StateDir is where flyballd keeps its state when flyballd.yaml names no
// data_dir or manifests_dir: $STATE_DIRECTORY (systemd's
// StateDirectory=flyball; the first, if it lists several), else
// DefaultStateDir.
func StateDir() string {
	if sd, _, _ := strings.Cut(os.Getenv("STATE_DIRECTORY"), ":"); sd != "" {
		return sd
	}
	return DefaultStateDir
}

// UnderConfig makes dir, read from the flyballd.yaml at configPath,
// absolute: a relative one is under that file's directory.
func UnderConfig(configPath, dir string) string {
	if filepath.IsAbs(dir) {
		return dir
	}
	base := filepath.Dir(configPath)
	if abs, err := filepath.Abs(base); err == nil {
		base = abs
	}
	return filepath.Join(base, dir)
}

// DefaultDaemonConfig is flyballd's config when flyballd.yaml says
// nothing: its data in StateDir(), its manifests in StateDir()/manifests.
func DefaultDaemonConfig() DaemonConfig {
	state := StateDir()
	return DaemonConfig{
		Front:        front.Config{Listen: DefaultListen},
		ManifestsDir: filepath.Join(state, "manifests"),
		DataDir:      state,
		LogMaxSize:   10 << 20, // 10MiB, a placeholder default
	}
}

// daemonKeys are flyballd.yaml's own keys; every other top-level key is
// the front's.
var daemonKeys = []string{"default_server", "manifests_dir", "data_dir", "log_max_size"}

// LoadDaemonConfig reads flyballd.yaml. A bad daemon key is an error; a
// front block that cannot be read is FrontError, never an error.
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
	cfg.ManifestsDir = UnderConfig(path, cfg.ManifestsDir)
	cfg.DataDir = UnderConfig(path, cfg.DataDir)
	var top map[string]any
	if err := yaml.Unmarshal(data, &top); err != nil {
		return cfg, fmt.Errorf("parsing daemon config %s: %w", path, err)
	}
	for _, k := range daemonKeys {
		delete(top, k)
	}
	listen, _ := top["listen"].(string)
	if _, old := top["auth"].(map[string]any); old {
		cfg.FrontError = errors.New("auth: {token, insecure_open} is gone: management takes a bearer token with" +
			" the management scope (`flyball token create --scope manage`), --insecure-open is a flag of the run," +
			" and auth: names the front's shape (local, password, proxy)")
		cfg.Front = front.Config{Listen: listen}
	} else if f, err := frontwire.Decode(top); err != nil {
		cfg.FrontError = fmt.Errorf("%s: %w", path, err)
		cfg.Front = front.Config{Listen: listen}
	} else {
		cfg.Front = f
	}
	if cfg.Front.Listen == "" {
		cfg.Front.Listen = DefaultListen
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
	// Network is how flyballd reaches the runner: "unix" (a socket in its
	// 0700 front-dir) or "tcp" (loopback Host:Port). "" is unix, or tcp on
	// Windows, where uvicorn has no unix sockets. tcp is Windows only
	// (D-044): elsewhere a manifest that asks for it loads, and the
	// backend runs the runner on unix and logs why.
	Network  string `yaml:"network" json:"network"`
	Host     string `yaml:"host" json:"host"` // loopback only (default 127.0.0.1); meaningful only for tcp
	Port     int    `yaml:"port" json:"port"` // required only when the network resolves to tcp, on Windows
	RootPath string `yaml:"root_path" json:"root_path"`
	Store    string `yaml:"store" json:"store"`
	Enabled  *bool  `yaml:"enabled" json:"enabled"`
	// RemovedAnonymous catches the manifest's anonymous:, removed: it was
	// checked and then read by nothing. Set at all, Validate refuses it
	// and names the front-wide anonymous: in flyballd.yaml.
	RemovedAnonymous *string `yaml:"anonymous" json:"anonymous,omitempty"`
	// UvProject, when set, launches flyball-runner via `uv run --project
	// UvProject flyball-runner ...` instead of execing it bare -- needed
	// whenever flyball-runner isn't already on flyballd's own $PATH, which
	// it never is outside an app's own uv-managed venv (same problem, same
	// fix, as `flyball run`'s --uv flag).
	UvProject string `yaml:"uv_project" json:"uv_project"`
}

// ResolvedNetwork is Network with its default applied: "unix", or
// "tcp" on Windows. It is what was asked: off Windows the backend runs a
// tcp runner on unix (D-044).
func (m Manifest) ResolvedNetwork() string {
	if m.Network != "" {
		return m.Network
	}
	if endpoint.GOOS == "windows" {
		return "tcp"
	}
	return "unix"
}

func (m Manifest) IsEnabled() bool {
	return m.Enabled == nil || *m.Enabled
}

var (
	namePattern     = names.Pattern // the engine's key grammar; `-` is `_` (D-079)
	rootPathPattern = regexp.MustCompile(`^(/[a-z0-9][a-z0-9_-]*)+$`)
)

// Validate refuses a manifest whose name or root path could reach outside
// its lane: the name becomes a log file name and a URL prefix, the root
// path a proxy prefix and a line of HTML.
func (m Manifest) Validate() error {
	if !namePattern.MatchString(m.Name) {
		return fmt.Errorf("runner name %q: %s", m.Name, names.Grammar)
	}
	if m.RemovedAnonymous != nil {
		return fmt.Errorf("runner %s: anonymous: is not a manifest key: what a caller with no credential may do is set once for every rig, by anonymous: in flyballd.yaml", m.Name)
	}
	if m.ServerConfig == "" {
		return fmt.Errorf("runner %s: server_config is required", m.Name)
	}
	if !isLoopback(m.Host) {
		return fmt.Errorf("runner %s: host %q is not a loopback address: a daemon-supervised runner listens on 127.0.0.1 (the default) or ::1 and is reached through flyballd's proxy under its root_path", m.Name, m.Host)
	}
	switch m.Network {
	case "", "tcp":
	case "unix":
		if endpoint.GOOS == "windows" {
			return fmt.Errorf("runner %s: network unix: not on Windows, where the runner has no unix sockets; use tcp", m.Name)
		}
	default:
		return fmt.Errorf("runner %s: network %q: use unix (the default) or tcp", m.Name, m.Network)
	}
	if m.Port < 0 || m.Port > 65535 {
		return fmt.Errorf("runner %s: port %d is not a TCP port", m.Name, m.Port)
	}
	if m.ResolvedNetwork() == "tcp" && endpoint.TCPAllowed() && m.Port == 0 {
		return fmt.Errorf("runner %s: network tcp needs a port", m.Name)
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
