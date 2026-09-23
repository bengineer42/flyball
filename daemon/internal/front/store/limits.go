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

import "time"

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
