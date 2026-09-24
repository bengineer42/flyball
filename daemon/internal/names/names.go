// Package names is the key grammar for a runner's (a rig's) name, the same
// one the engine enforces (flyball.foundation.keys, D-077): lower-case
// letters, digits and _, starting with a letter, at most 64. `-` and `_` are
// one character in a name (D-079): either is accepted, and the canonical
// form, the one registered and compared, has `_`. So a manifest named
// humidity-sim is the runner humidity_sim, found by either spelling.
//
// Nothing the daemon makes from a name today allows only `-` (a log file
// name, a URL prefix and an audience allow both), so nothing turns `_` back
// into `-`; a DNS label would be the first such edge.
package names

import (
	"regexp"
	"strings"
)

// Pattern is what a name may be as given: `-` or `_` between the letters.
var Pattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,63}$`)

// Grammar says Pattern in words, for an error.
const Grammar = "lower-case letters, digits and _ (or -), starting with a letter, up to 64"

// Canonical is name with every - as _: the form a name is kept and compared in.
func Canonical(name string) string {
	return strings.ReplaceAll(name, "-", "_")
}

// Valid is whether name is a name, in either spelling.
func Valid(name string) bool {
	return Pattern.MatchString(name)
}
