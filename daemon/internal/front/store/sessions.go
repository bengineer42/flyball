package store

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"slices"
	"sync"
	"time"
)

// Session is one sign-in. The cookie that names it is never stored: the
// store keeps only its SHA-256, and Sid is a separate random id (F9), so no
// log line, principal or cancel-registry key that carries the sid lets
// anyone rebuild the cookie, or the other way round.
type Session struct {
	Sid     string
	Subject string   // who signed in, as the front names it (Phase 1: the admin password)
	Scopes  []string // opaque (the vocabulary is pending D-034); stored and returned as given
	Created time.Time
	Seen    time.Time // the last Create, Lookup or Seen
}

// SessionOptions configures a Sessions. The zero value is the design's
// defaults on the real clock.
type SessionOptions struct {
	Idle     time.Duration    // 0: SessionIdle
	Absolute time.Duration    // 0: SessionAbsolute
	Now      func() time.Time // nil: time.Now, whose monotonic reading ignores wall-clock steps
	// OnEnd is called, outside the store's lock, once for every session that
	// ends: logout (Delete), and expiry found by Lookup, Seen or Sweep. The
	// front cancels that sid's websockets and streams from it.
	OnEnd func(sid string)
}

// Sessions holds sign-ins in memory only: a front restart signs everyone
// out. It is safe for concurrent use.
type Sessions struct {
	idle, absolute time.Duration
	now            func() time.Time
	onEnd          func(string)

	mu     sync.Mutex
	byHash map[[sha256.Size]byte]*Session
	bySid  map[string][sha256.Size]byte
}

// NewSessions returns an empty Sessions.
func NewSessions(o SessionOptions) *Sessions {
	s := &Sessions{
		idle: o.Idle, absolute: o.Absolute, now: o.Now, onEnd: o.OnEnd,
		byHash: map[[sha256.Size]byte]*Session{}, bySid: map[string][sha256.Size]byte{},
	}
	if s.idle <= 0 {
		s.idle = SessionIdle
	}
	if s.absolute <= 0 {
		s.absolute = SessionAbsolute
	}
	if s.now == nil {
		s.now = time.Now
	}
	return s
}

// Create starts a session and returns its cookie value: 32 random bytes,
// base64url without padding. The front calls it at every login, so a
// session id is never reused across sign-ins.
func (s *Sessions) Create(subject string, scopes []string) (cookie string, _ Session, _ error) {
	secret, sid := make([]byte, 32), make([]byte, 16)
	if _, err := rand.Read(secret); err != nil {
		return "", Session{}, err
	}
	if _, err := rand.Read(sid); err != nil {
		return "", Session{}, err
	}
	cookie = b64(secret)
	now := s.now()
	sess := &Session{
		Sid: hex.EncodeToString(sid), Subject: subject, Scopes: slices.Clone(scopes),
		Created: now, Seen: now,
	}
	key := sha256.Sum256([]byte(cookie))
	s.mu.Lock()
	s.byHash[key] = sess
	s.bySid[sess.Sid] = key
	out := sess.copy()
	s.mu.Unlock()
	return cookie, out, nil
}

// Lookup returns the live session the cookie names and marks it seen. An
// unknown cookie, or one whose session has expired, is not found; an
// expired one is removed (and OnEnd called).
func (s *Sessions) Lookup(cookie string) (Session, bool) {
	key := sha256.Sum256([]byte(cookie))
	now := s.now()
	s.mu.Lock()
	// A map lookup, not a constant-time scan: what it could leak through
	// timing is the SHA-256 of a 256-bit random secret, which gives nothing
	// towards the cookie. (Tokens, few and long-lived, are scanned in
	// constant time; see Tokens.Lookup.)
	sess, ok := s.byHash[key]
	if !ok {
		s.mu.Unlock()
		return Session{}, false
	}
	if s.expired(sess, now) {
		s.removeLocked(sess.Sid)
		s.mu.Unlock()
		s.end(sess.Sid)
		return Session{}, false
	}
	sess.Seen = now
	out := sess.copy()
	s.mu.Unlock()
	return out, true
}

// Seen marks a session used (a websocket frame, a streamed response) and
// reports whether it is still live. It never recreates a session: after
// Delete or expiry it is false and changes nothing.
func (s *Sessions) Seen(sid string) bool {
	now := s.now()
	s.mu.Lock()
	key, ok := s.bySid[sid]
	if !ok {
		s.mu.Unlock()
		return false
	}
	sess := s.byHash[key]
	if s.expired(sess, now) {
		s.removeLocked(sid)
		s.mu.Unlock()
		s.end(sid)
		return false
	}
	sess.Seen = now
	s.mu.Unlock()
	return true
}

// Delete ends a session (logout) and reports whether it existed. OnEnd is
// called only when it did.
func (s *Sessions) Delete(sid string) bool {
	s.mu.Lock()
	_, ok := s.bySid[sid]
	if ok {
		s.removeLocked(sid)
	}
	s.mu.Unlock()
	if ok {
		s.end(sid)
	}
	return ok
}

// Sweep removes every expired session and returns their sids, calling OnEnd
// for each. The front runs it about once a second, so that an expired
// session's open websockets close within a second even if nothing looks the
// session up.
func (s *Sessions) Sweep() []string {
	now := s.now()
	var gone []string
	s.mu.Lock()
	for sid, key := range s.bySid {
		if s.expired(s.byHash[key], now) {
			s.removeLocked(sid)
			gone = append(gone, sid)
		}
	}
	s.mu.Unlock()
	for _, sid := range gone {
		s.end(sid)
	}
	return gone
}

// Len is how many sessions are held (expired ones not yet swept included).
func (s *Sessions) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.bySid)
}

func (s *Sessions) expired(sess *Session, now time.Time) bool {
	return now.Sub(sess.Seen) >= s.idle || now.Sub(sess.Created) >= s.absolute
}

func (s *Sessions) removeLocked(sid string) {
	delete(s.byHash, s.bySid[sid])
	delete(s.bySid, sid)
}

func (s *Sessions) end(sid string) {
	if s.onEnd != nil {
		s.onEnd(sid)
	}
}

func (sess *Session) copy() Session {
	out := *sess
	out.Scopes = slices.Clone(sess.Scopes)
	return out
}
