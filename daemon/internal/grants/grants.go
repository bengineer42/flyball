// Package grants is the front's side of the verb vocabulary: which verbs
// exist, which verbs a grant bundles, how a stored scope names verbs on a
// rig, and what a proxy-asserted identity is granted.
//
// The vocabulary is pending D-034 (brain design auth.md § Permissions).
// Everything here reads vocabulary.json, embedded at build time, so the
// D-034 round edits that file and nothing in this package's code. Scopes
// are opaque strings to everything past this package: the principal
// carries verbs already expanded, and the runner checks membership only.
//
// A stored scope (a named token's ceiling, a session's, a grant's) is
// `<verb>:<rig>`, `<verb>:*` for every rig, or the bare management string
// (flyballd's non-rig scope). A bare verb is accepted on input as
// `<verb>:*`.
package grants

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
)

//go:embed vocabulary.json
var vocabularyJSON []byte

// Vocabulary is vocabulary.json.
type Vocabulary struct {
	Version    int                 `json:"version"`
	Pending    string              `json:"pending"`
	Vocabulary []string            `json:"vocabulary"`
	Grants     map[string][]string `json:"grants"`
	Management string              `json:"management"`
}

// Read is the read verb. Every candidate vocabulary has it, and it is what a
// proxy-authenticated user who matches no grant gets (Ben, 23 Sep).
const Read = "read"

// AllRigs is the rig part of a scope that covers every rig.
const AllRigs = "*"

// AllGrant is the grant that bundles every verb: the admin password, the
// local shape, and CLI-issued tokens' issuer.
const AllGrant = "all"

var vocab = func() Vocabulary {
	var v Vocabulary
	if err := json.Unmarshal(vocabularyJSON, &v); err != nil {
		panic("grants: vocabulary.json: " + err.Error())
	}
	if v.Version != 1 || v.Management == "" || !slices.Contains(v.Vocabulary, Read) || len(v.Grants[AllGrant]) == 0 {
		panic("grants: vocabulary.json lacks version 1, management, the read verb or the all grant")
	}
	slices.Sort(v.Vocabulary)
	return v
}()

// Vocab is the parsed vocabulary.json (a copy).
func Vocab() Vocabulary {
	v := vocab
	v.Vocabulary = slices.Clone(v.Vocabulary)
	v.Grants = make(map[string][]string, len(vocab.Grants))
	for name, verbs := range vocab.Grants {
		v.Grants[name] = slices.Clone(verbs)
	}
	return v
}

// Verbs is the vocabulary, sorted.
func Verbs() []string { return slices.Clone(vocab.Vocabulary) }

// Management is the non-rig scope flyballd's management routes need.
func Management() string { return vocab.Management }

// IsVerb reports whether v is in the vocabulary.
func IsVerb(v string) bool { return slices.Contains(vocab.Vocabulary, v) }

// Expand is the verbs the grant named name bundles, sorted; nil for a grant the
// vocabulary does not name.
func Expand(name string) []string {
	verbs, ok := vocab.Grants[name]
	if !ok {
		return nil
	}
	return Normalize(verbs)
}

// All is every verb on every rig, as scopes.
func All() []string { return Scopes(Verbs(), AllRigs) }

// Scopes is verbs on rig, as scopes (`<verb>:<rig>`), sorted.
func Scopes(verbs []string, rig string) []string {
	out := make([]string, 0, len(verbs))
	for _, v := range verbs {
		out = append(out, v+":"+rig)
	}
	return Normalize(out)
}

// Scope is one parsed stored scope.
type Scope struct {
	Verb string // "" for the management scope
	Rig  string // a rig name or AllRigs; "" for the management scope
}

// Management reports whether s is the management scope.
func (s Scope) Management() bool { return s.Verb == "" }

func (s Scope) String() string {
	if s.Management() {
		return vocab.Management
	}
	return s.Verb + ":" + s.Rig
}

// ParseScope reads one scope: `<verb>:<rig>`, `<verb>:*`, a bare verb
// (`<verb>:*`), or the management string. The verb must be in the
// vocabulary; a rig name is letters, digits, '.', '_' and '-'.
func ParseScope(s string) (Scope, error) {
	if s == vocab.Management {
		return Scope{}, nil
	}
	verb, rig, qualified := strings.Cut(s, ":")
	if !qualified {
		rig = AllRigs
	}
	if !IsVerb(verb) {
		return Scope{}, fmt.Errorf("scope %q: %q is not a verb (the vocabulary is %s, or %q for management)",
			s, verb, strings.Join(vocab.Vocabulary, ", "), vocab.Management)
	}
	if rig != AllRigs && !rigName(rig) {
		return Scope{}, fmt.Errorf("scope %q: %q is not a rig name or *", s, rig)
	}
	return Scope{Verb: verb, Rig: rig}, nil
}

func rigName(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range s {
		if !('a' <= c && c <= 'z' || 'A' <= c && c <= 'Z' || '0' <= c && c <= '9' || c == '.' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}

// NormalizeScopes parses every scope and returns their canonical forms,
// sorted and unique; never nil. The first bad scope is the error.
func NormalizeScopes(scopes []string) ([]string, error) {
	out := make([]string, 0, len(scopes))
	for _, s := range scopes {
		p, err := ParseScope(s)
		if err != nil {
			return nil, err
		}
		out = append(out, p.String())
	}
	return Normalize(out), nil
}

// HasManagement reports whether scopes include the management scope.
func HasManagement(scopes []string) bool { return slices.Contains(scopes, vocab.Management) }

// ForRig is the verbs scopes give on rig, sorted and unique; never nil. A
// scope that does not parse, or names another rig, or is the management
// scope, gives nothing.
func ForRig(scopes []string, rig string) []string {
	out := []string{}
	for _, s := range scopes {
		p, err := ParseScope(s)
		if err != nil || p.Management() {
			continue
		}
		if p.Rig == AllRigs || p.Rig == rig {
			out = append(out, p.Verb)
		}
	}
	return Normalize(out)
}

// Intersect is the strings in both a and b, sorted and unique; never nil.
func Intersect(a, b []string) []string {
	out := []string{}
	for _, s := range a {
		if slices.Contains(b, s) {
			out = append(out, s)
		}
	}
	return Normalize(out)
}

// Normalize sorts and de-duplicates; never nil.
func Normalize(s []string) []string {
	out := append([]string{}, s...)
	slices.Sort(out)
	return slices.Compact(out)
}

// GroupPrefix marks a grant entry that names a group, not a subject.
const GroupPrefix = "group:"

// Match is what a proxy-authenticated identity is granted, as scopes over
// every rig: the union of the grants whose entries name its subject or, as
// `group:<id>`, one of its groups; `read` on every rig when none does. The
// map is `proxy.grants` (grant name -> entries). A grant the vocabulary does
// not name grants nothing (UnknownGrants reports those at startup).
func Match(grants map[string][]string, subject string, groups []string) []string {
	var verbs []string
	for name, entries := range grants {
		for _, e := range entries {
			hit := false
			if g, isGroup := strings.CutPrefix(e, GroupPrefix); isGroup {
				hit = slices.Contains(groups, g)
			} else {
				hit = e == subject
			}
			if hit {
				verbs = append(verbs, Expand(name)...)
				break
			}
		}
	}
	if len(verbs) == 0 {
		verbs = []string{Read}
	}
	return Scopes(verbs, AllRigs)
}

// UnknownGrants is the grant names in grants the vocabulary does not name, sorted.
func UnknownGrants(grants map[string][]string) []string {
	var out []string
	for name := range grants {
		if _, ok := vocab.Grants[name]; !ok {
			out = append(out, name)
		}
	}
	slices.Sort(out)
	return out
}
