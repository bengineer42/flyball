package rigfile

import (
	"fmt"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"

	"flyballd/internal/names"
)

// named are the sections keyed by names, whose `-` and `_` spellings are one
// key (D-079). Matches flyball.runtime.overlay.NAMED.
var named = []string{"links", "devices", "controllers"}

func isNamed(section string) bool {
	for _, n := range named {
		if n == section {
			return true
		}
	}
	return false
}

// canonicalNames returns document with the names under links, devices and
// controllers in canonical form, so `dry-air` in one layer and `dry_air` in
// the next are one entry. Both spellings in one section of one layer is an
// error. Matches flyball.runtime.overlay._canonical_names.
func canonicalNames(document map[string]any, source string) (map[string]any, error) {
	result := document
	for _, section := range named {
		entries, ok := document[section].(map[string]any)
		if !ok {
			continue
		}
		renamed := make(map[string]any, len(entries))
		changed := false
		for key, value := range entries {
			name := names.Canonical(key)
			if name != key {
				changed = true
			}
			if _, dup := renamed[name]; dup {
				return nil, fmt.Errorf("%s: %s: %q and %q are one name, given twice", source, section, key, name)
			}
			renamed[name] = value
		}
		if !changed {
			continue
		}
		copied := make(map[string]any, len(result))
		for k, v := range result {
			copied[k] = v
		}
		copied[section] = renamed
		result = copied
	}
	return result, nil
}

// Merge overlays overlay onto base: mappings deep-merge, everything else
// replaces. A key whose overlay value is nil is removed from the result --
// the only way to delete something an earlier layer set. Neither argument
// is mutated. Matches flyball.runtime.overlay.merge.
func Merge(base, overlay map[string]any) map[string]any {
	result := make(map[string]any, len(base))
	for k, v := range base {
		result[k] = v
	}
	for key, value := range overlay {
		if value == nil {
			delete(result, key)
			continue
		}
		if ov, ok := value.(map[string]any); ok {
			if bv, ok := result[key].(map[string]any); ok {
				result[key] = Merge(bv, ov)
				continue
			}
		}
		result[key] = value
	}
	return result
}

// ParseSet parses "devices.furnace.noise=0.3" into
// (["devices","furnace","noise"], 0.3). The value is parsed as a
// YAML scalar: "0.3" -> float64, "true" -> bool, "null" -> nil (delete,
// once applied), "[1, 2]" -> []any, a bare word -> string. Matches
// flyball.runtime.overlay.parse_set.
func ParseSet(expr string) ([]string, any, error) {
	key, raw, ok := strings.Cut(expr, "=")
	if !ok {
		return nil, nil, fmt.Errorf("%s: expected KEY=VALUE", expr)
	}
	var value any
	if err := yaml.Unmarshal([]byte(raw), &value); err != nil {
		return nil, nil, err
	}
	return strings.Split(key, "."), normalise(value), nil
}

// ApplySet returns document with value set at path, creating intermediate
// mappings as needed. value of nil deletes the key at path instead;
// deleting a path that is not there is a no-op. Pure: document is not
// mutated. Matches flyball.runtime.overlay.apply_set.
func ApplySet(document map[string]any, path []string, value any) (map[string]any, error) {
	if len(path) == 0 {
		return nil, fmt.Errorf("empty path")
	}
	if len(path) > 1 && isNamed(path[0]) {
		path = append([]string{path[0], names.Canonical(path[1])}, path[2:]...)
	}
	head, rest := path[0], path[1:]
	if len(rest) == 0 {
		result := make(map[string]any, len(document)+1)
		for k, v := range document {
			result[k] = v
		}
		if value == nil {
			delete(result, head)
		} else {
			result[head] = value
		}
		return result, nil
	}
	child, isMap := document[head].(map[string]any)
	if value == nil && !isMap {
		return document, nil // nothing at this path to delete into: a true no-op
	}
	if !isMap {
		child = map[string]any{}
	}
	nested, err := ApplySet(child, rest, value)
	if err != nil {
		return nil, err
	}
	result := make(map[string]any, len(document)+1)
	for k, v := range document {
		result[k] = v
	}
	result[head] = nested
	return result, nil
}

// loadLayer reads path, resolving and stripping its own `extends`, matching
// flyball.runtime.overlay._load_layer. stack carries the resolved paths of
// files currently being loaded, to detect an extends cycle; seen, the files
// already merged in this resolution: each file is merged once, where it is
// first reached (depth first), so a file two bases share cannot come back
// over an earlier base's changes.
func loadLayer(path string, stack []string, seen map[string]bool) (map[string]any, []string, error) {
	resolved, err := filepath.Abs(path)
	if err != nil {
		return nil, nil, err
	}
	for _, s := range stack {
		if s == resolved {
			return nil, nil, fmt.Errorf("extends cycle: %s -> %s", strings.Join(append(stack, resolved), " -> "), resolved)
		}
	}
	if seen[resolved] {
		return map[string]any{}, nil, nil
	}
	seen[resolved] = true
	doc, err := LoadDocument(path)
	if err != nil {
		return nil, nil, err
	}
	document, ok := doc.(map[string]any)
	if !ok {
		return nil, nil, fmt.Errorf("%s: a rig file is a mapping", path)
	}
	var extends []string
	if raw, ok := document["extends"]; ok {
		list, ok := raw.([]any)
		if !ok {
			return nil, nil, fmt.Errorf("%s: extends must be a list of paths", path)
		}
		for _, e := range list {
			s, ok := e.(string)
			if !ok {
				return nil, nil, fmt.Errorf("%s: extends must be a list of paths", path)
			}
			extends = append(extends, s)
		}
	}
	base := map[string]any{}
	var contributed []string
	nextStack := append(append([]string{}, stack...), resolved)
	for _, name := range extends {
		if !filepath.IsAbs(name) { // an absolute name is itself, as Python's path.parent / name
			name = filepath.Join(filepath.Dir(path), name)
		}
		baseDoc, baseFiles, err := loadLayer(name, nextStack, seen)
		if err != nil {
			return nil, nil, err
		}
		base = Merge(base, baseDoc)
		contributed = appendUnique(contributed, baseFiles...)
	}
	own := map[string]any{}
	for k, v := range document {
		if k != "extends" {
			own[k] = v
		}
	}
	own, err = canonicalNames(own, path)
	if err != nil {
		return nil, nil, err
	}
	contributed = appendUnique(contributed, path)
	return Merge(base, own), contributed, nil
}

func appendUnique(list []string, items ...string) []string {
	for _, item := range items {
		found := false
		for _, l := range list {
			if l == item {
				found = true
				break
			}
		}
		if !found {
			list = append(list, item)
		}
	}
	return list
}

// ResolveLayers merges every file in paths, each with its own extends
// resolved, in order -- later files overlay earlier ones. Every --set in
// sets is applied last, in order. Matches
// flyball.runtime.overlay.resolve_layers (the board-lookup step in
// resolve_documents is not reproduced here -- board profiles are a
// separate, not-yet-scoped piece).
func ResolveLayers(paths []string, sets []string) (map[string]any, []string, error) {
	document := map[string]any{}
	var contributed []string
	seen := map[string]bool{}
	for _, path := range paths {
		layer, files, err := loadLayer(path, nil, seen)
		if err != nil {
			return nil, nil, err
		}
		document = Merge(document, layer)
		contributed = appendUnique(contributed, files...)
	}
	for _, expr := range sets {
		setPath, value, err := ParseSet(expr)
		if err != nil {
			return nil, nil, err
		}
		document, err = ApplySet(document, setPath, value)
		if err != nil {
			return nil, nil, err
		}
	}
	return document, contributed, nil
}
