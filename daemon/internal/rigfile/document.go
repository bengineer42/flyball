// Package rigfile loads and layers rig files the same way
// engine/src/flyball/runtime/overlay.py and engine/src/flyball/core/files.py
// do on the Python side: read a document by its suffix (.yaml/.yml/.json/
// .toml), resolve `extends` recursively per-file before merging into the
// command-line file sequence (docker-compose `-f` semantics -- later on the
// command line wins), apply `--set KEY=VALUE` overlays last, then run the
// hand-written cross-field checks JSON Schema alone can't express.
package rigfile

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/BurntSushi/toml"
	"gopkg.in/yaml.v3"
)

// Suffixes lists the recognised rig file extensions, matching
// engine/src/flyball/core/files.py's SUFFIXES.
var Suffixes = []string{".yaml", ".yml", ".json", ".toml"}

func suffixKnown(suffix string) bool {
	suffix = strings.ToLower(suffix)
	for _, s := range Suffixes {
		if s == suffix {
			return true
		}
	}
	return false
}

// LoadDocument reads path's plain data by its suffix, matching
// flyball.core.files.load_document.
func LoadDocument(path string) (any, error) {
	suffix := strings.ToLower(filepath.Ext(path))
	if !suffixKnown(suffix) {
		return nil, fmt.Errorf("%s: unknown format; use one of %s", path, strings.Join(Suffixes, ", "))
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return loadBytes(data, suffix)
}

func loadBytes(data []byte, suffix string) (any, error) {
	switch strings.ToLower(suffix) {
	case ".toml":
		var out any
		if err := toml.Unmarshal(data, &out); err != nil {
			return nil, err
		}
		return normalise(out), nil
	case ".yaml", ".yml":
		var out any
		if err := yaml.Unmarshal(data, &out); err != nil {
			return nil, err
		}
		return normalise(out), nil
	case ".json":
		var out any
		if err := json.Unmarshal(data, &out); err != nil {
			return nil, err
		}
		return normalise(out), nil
	}
	return nil, fmt.Errorf("unknown document format %q; use one of %s", suffix, strings.Join(Suffixes, ", "))
}

// normalise walks a decoded document turning any map[interface{}]any (which
// some decoders may still produce) into map[string]any, and
// []interface{} elements recursively, so downstream code (jsonschema,
// json.Marshal, our own map-walks) sees a consistent, JSON-shaped tree.
func normalise(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, val := range t {
			out[k] = normalise(val)
		}
		return out
	case map[any]any:
		out := make(map[string]any, len(t))
		for k, val := range t {
			out[fmt.Sprintf("%v", k)] = normalise(val)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, val := range t {
			out[i] = normalise(val)
		}
		return out
	default:
		return v
	}
}

// Dumps serialises data as the format suffix names, matching
// flyball.core.files.dumps: block-style YAML, indented JSON, and TOML with
// keys in the order given.
func Dumps(data any, suffix string) (string, error) {
	switch strings.ToLower(suffix) {
	case ".toml":
		var buf strings.Builder
		enc := toml.NewEncoder(&buf)
		if err := enc.Encode(data); err != nil {
			return "", err
		}
		return buf.String(), nil
	case ".yaml", ".yml":
		var buf strings.Builder
		enc := yaml.NewEncoder(&buf)
		enc.SetIndent(2)
		if err := enc.Encode(data); err != nil {
			return "", err
		}
		enc.Close()
		return buf.String(), nil
	case ".json":
		b, err := json.MarshalIndent(data, "", "  ")
		if err != nil {
			return "", err
		}
		return string(b) + "\n", nil
	}
	return "", fmt.Errorf("unknown document format %q; use one of %s", suffix, strings.Join(Suffixes, ", "))
}
