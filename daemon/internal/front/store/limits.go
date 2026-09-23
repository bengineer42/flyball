// Package store is the front's state: the named-tokens file, the in-memory
// sessions, the per-peer login limiter and the password-hashing semaphore
// (brain design auth.md § "Sessions and machine access"; Phase 1 package A3).
//
// Phase 1 has no user accounts: one admin password (a `$scrypt$` line from
// the front's config), named tokens in a 0600 file the front owns, and
// sessions that live only in memory, so a front restart signs everyone out.
//
// Every lifetime and bound the package uses is a named constant in this
// file, so that changing one is a one-line edit.
package store

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Token lifetimes. AWAITING BEN'S CONFIRMATION: these are the design's
// proposal (auth.md § "Sessions and machine access"), not yet a decision.
// Every token expires; there is no "never".
const (
	// TokenLifetimeDefault is a token's lifetime when its creator asks for none.
	TokenLifetimeDefault = 90 * 24 * time.Hour
	// TokenLifetimeCapped is the most a token may have when it is created
	// over cleartext (no TLS) or has kind "agent"; asking for more gets this.
	TokenLifetimeCapped = 30 * 24 * time.Hour
	// TokenLifetimeMax is the most any other token may have; asking for more
	// gets this. The design names no ceiling here: one year is this
	// package's choice, so that "mandatory expiry" cannot be defeated by
	// asking for a century.
	TokenLifetimeMax = 365 * 24 * time.Hour
	// LastUsedCoalesce is how far a token's last use must have moved before
	// it is written back to the file: a token used every second does not
	// rewrite the file every second.
	LastUsedCoalesce = 10 * time.Minute
)

// Lifetimes is a token store's effective default and max lifetime -- what
// TokenLifetime uses in place of TokenLifetimeDefault/TokenLifetimeMax. The
// zero value is not valid on its own; DefaultLifetimes gives the built-ins,
// and ResolveLifetimes gives a config-tightened pair that never exceeds
// them.
type Lifetimes struct {
	Default time.Duration
	Max     time.Duration
}

// DefaultLifetimes is the built-in values: the ceiling every configured
// `tokens:` value is checked against.
func DefaultLifetimes() Lifetimes {
	return Lifetimes{Default: TokenLifetimeDefault, Max: TokenLifetimeMax}
}

// ResolveLifetimes parses a front's `tokens: {default_lifetime,
// max_lifetime}` block ("" for either: not set, the built-in applies) into
// the effective Lifetimes, plus a warning for each value that falls back.
//
// The built-ins are the ceiling: max_lifetime may only tighten
// TokenLifetimeMax, never exceed it, and default_lifetime must be positive
// and at most the (already-resolved) effective max. Any invalid value --
// unparseable, <= 0, max above the built-in max, or default above the
// effective max -- falls back to that field's built-in, with one warning;
// it never refuses to start (D-028).
func ResolveLifetimes(defaultLifetime, maxLifetime string) (Lifetimes, []string) {
	lt := DefaultLifetimes()
	var warnings []string
	if maxLifetime != "" {
		d, err := ParseDuration(maxLifetime)
		if err != nil || d <= 0 || d > TokenLifetimeMax {
			warnings = append(warnings, fmt.Sprintf(
				"tokens.max_lifetime: %q is not a duration between 0 and the built-in ceiling %s; using %s",
				maxLifetime, TokenLifetimeMax, TokenLifetimeMax))
		} else {
			lt.Max = d
		}
	}
	if defaultLifetime != "" {
		d, err := ParseDuration(defaultLifetime)
		if err != nil || d <= 0 || d > lt.Max {
			warnings = append(warnings, fmt.Sprintf(
				"tokens.default_lifetime: %q is not a duration between 0 and the effective max %s; using %s",
				defaultLifetime, lt.Max, TokenLifetimeDefault))
		} else {
			lt.Default = d
		}
	}
	return lt, warnings
}

// ParseDuration is time.ParseDuration plus a whole-days form ("30d", "90d"):
// Go's own syntax has no unit past hours. Shared by the front config
// (session, tokens.default_lifetime/max_lifetime) and the offline `flyball
// token create --expires`.
func ParseDuration(s string) (time.Duration, error) {
	if days, ok := strings.CutSuffix(s, "d"); ok {
		n, err := strconv.Atoi(days)
		if err != nil {
			return 0, fmt.Errorf("%q: %w", s, err)
		}
		return time.Duration(n) * 24 * time.Hour, nil
	}
	return time.ParseDuration(s)
}

// Session lifetimes (auth.md: idle 12 h, today's default; absolute 7 days).
const (
	SessionIdle     = 12 * time.Hour
	SessionAbsolute = 7 * 24 * time.Hour
)

// Password checks (auth.md; the runner's sec-hardening rules, ported).
const (
	// HashingSlots is how many password hashes may run at once; one more is
	// refused with ErrBusy rather than queued (scrypt is 16 MiB each, and a
	// Pi has little memory to spare).
	HashingSlots = 2
	// LoginAttempts failures from one peer within LoginWindow hold that peer
	// off until the oldest falls out of the window: a delay, never a ban.
	LoginAttempts = 10
	LoginWindow   = time.Minute
	// LimiterEntries bounds the limiter's memory: past it, the least recently
	// failed peer is forgotten.
	LimiterEntries = 10_000
)
