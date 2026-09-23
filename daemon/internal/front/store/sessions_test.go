package store

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

type ended struct {
	mu   sync.Mutex
	sids []string
}

func (e *ended) record(sid string) {
	e.mu.Lock()
	e.sids = append(e.sids, sid)
	e.mu.Unlock()
}

func (e *ended) list() []string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return slices.Clone(e.sids)
}

func newTestSessions() (*Sessions, *fakeClock, *ended) {
	clock, gone := newFakeClock(), &ended{}
	return NewSessions(SessionOptions{Now: clock.Now, OnEnd: gone.record}), clock, gone
}

func TestSessionsIdle(t *testing.T) {
	s, clock, gone := newTestSessions()
	cookie, sess, err := s.Create("password", []string{"operate"})
	if err != nil {
		t.Fatal(err)
	}
	clock.Advance(SessionIdle - time.Second)
	if _, ok := s.Lookup(cookie); !ok {
		t.Fatalf("session gone after %v idle; want alive until %v", SessionIdle-time.Second, SessionIdle)
	}
	clock.Advance(SessionIdle - time.Second) // measured from the last Lookup
	if _, ok := s.Lookup(cookie); !ok {
		t.Fatalf("session gone %v after its last use", SessionIdle-time.Second)
	}
	clock.Advance(SessionIdle)
	if _, ok := s.Lookup(cookie); ok {
		t.Fatalf("session alive after %v idle", SessionIdle)
	}
	if got := gone.list(); !slices.Equal(got, []string{sess.Sid}) {
		t.Errorf("OnEnd calls = %v; want [%s]", got, sess.Sid)
	}
	if n := s.Len(); n != 0 {
		t.Errorf("Len = %d after expiry; want 0", n)
	}
}

func TestSessionsAbsolute(t *testing.T) {
	s, clock, _ := newTestSessions()
	cookie, _, _ := s.Create("password", nil)
	for elapsed := time.Duration(0); elapsed < SessionAbsolute-time.Hour; elapsed += time.Hour {
		clock.Advance(time.Hour)
		if _, ok := s.Lookup(cookie); !ok {
			t.Fatalf("session gone after %v though used hourly; absolute is %v", elapsed+time.Hour, SessionAbsolute)
		}
	}
	clock.Advance(time.Hour)
	if _, ok := s.Lookup(cookie); ok {
		t.Fatalf("session alive at %v though used hourly; want the absolute limit", SessionAbsolute)
	}
}

func TestSessionsSweep(t *testing.T) {
	s, clock, gone := newTestSessions()
	_, old, _ := s.Create("password", nil)
	clock.Advance(SessionIdle / 2)
	young, fresh, _ := s.Create("password", nil)
	clock.Advance(SessionIdle / 2)
	if got := s.Sweep(); !slices.Equal(got, []string{old.Sid}) {
		t.Fatalf("Sweep = %v; want [%s]", got, old.Sid)
	}
	if got := gone.list(); !slices.Equal(got, []string{old.Sid}) {
		t.Errorf("OnEnd calls = %v; want [%s]", got, old.Sid)
	}
	if sess, ok := s.Lookup(young); !ok || sess.Sid != fresh.Sid {
		t.Errorf("the younger session was swept")
	}
	if got := s.Sweep(); len(got) != 0 {
		t.Errorf("second Sweep = %v; want nothing", got)
	}
}

func TestSessionsSidNotDerivable(t *testing.T) {
	s, _, _ := newTestSessions()
	seen := map[string]bool{}
	for range 1000 {
		cookie, sess, err := s.Create("password", nil)
		if err != nil {
			t.Fatal(err)
		}
		raw, err := base64.RawURLEncoding.DecodeString(cookie)
		if err != nil || len(raw) != 32 {
			t.Fatalf("cookie %q is not 32 bytes of base64url: %v", cookie, err)
		}
		sum := sha256.Sum256([]byte(cookie))
		for _, derived := range []string{cookie, hex.EncodeToString(sum[:]), base64.RawURLEncoding.EncodeToString(sum[:]), hex.EncodeToString(raw)} {
			if strings.Contains(derived, sess.Sid) || strings.Contains(sess.Sid, derived) {
				t.Fatalf("sid %q is derivable from cookie %q", sess.Sid, cookie)
			}
		}
		if len(sess.Sid) < 16 || seen[sess.Sid] {
			t.Fatalf("sid %q short or repeated", sess.Sid)
		}
		seen[sess.Sid] = true
	}
	// Only the cookie's SHA-256 is kept: the map is keyed by it.
	cookie, _, _ := s.Create("password", nil)
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.byHash[sha256.Sum256([]byte(cookie))]; !ok {
		t.Fatalf("no session under the cookie's SHA-256")
	}
}

func TestSessionsLogout(t *testing.T) {
	s, _, gone := newTestSessions()
	cookie, sess, _ := s.Create("password", nil)
	if !s.Delete(sess.Sid) {
		t.Fatalf("Delete(%s) = false", sess.Sid)
	}
	if _, ok := s.Lookup(cookie); ok {
		t.Errorf("Lookup after logout succeeded")
	}
	if s.Delete(sess.Sid) {
		t.Errorf("second Delete = true")
	}
	if got := gone.list(); !slices.Equal(got, []string{sess.Sid}) {
		t.Errorf("OnEnd calls = %v; want exactly one for %s", got, sess.Sid)
	}
}

func TestSessionsLateSeen(t *testing.T) {
	s, _, _ := newTestSessions()
	cookie, sess, _ := s.Create("password", nil)
	s.Delete(sess.Sid)
	if s.Seen(sess.Sid) {
		t.Errorf("Seen after delete = true")
	}
	if _, ok := s.Lookup(cookie); ok {
		t.Errorf("a late Seen revived the deleted session")
	}
	if n := s.Len(); n != 0 {
		t.Errorf("Len = %d after a late Seen; want 0", n)
	}
}

func TestSessionsSeenExtendsIdle(t *testing.T) {
	s, clock, _ := newTestSessions()
	cookie, sess, _ := s.Create("password", nil)
	clock.Advance(SessionIdle - time.Minute)
	if !s.Seen(sess.Sid) {
		t.Fatalf("Seen = false on a live session")
	}
	clock.Advance(SessionIdle - time.Minute)
	if _, ok := s.Lookup(cookie); !ok {
		t.Errorf("Seen did not reset the idle clock")
	}
}

func TestSessionsCopies(t *testing.T) {
	s, _, _ := newTestSessions()
	scopes := []string{"read", "operate"}
	cookie, sess, _ := s.Create("password", scopes)
	scopes[0], sess.Scopes[1] = "admin", "admin"
	got, _ := s.Lookup(cookie)
	if !slices.Equal(got.Scopes, []string{"read", "operate"}) {
		t.Errorf("stored scopes = %v; want them as given at Create", got.Scopes)
	}
	if _, ok := s.Lookup("not a cookie"); ok {
		t.Errorf("garbage cookie accepted")
	}
}

func TestSessionsConcurrent(t *testing.T) {
	s := NewSessions(SessionOptions{})
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range 200 {
				cookie, sess, err := s.Create("password", []string{"read"})
				if err != nil {
					t.Error(err)
					return
				}
				s.Lookup(cookie)
				s.Seen(sess.Sid)
				s.Sweep()
				s.Delete(sess.Sid)
				if _, ok := s.Lookup(cookie); ok {
					t.Error("Lookup after Delete succeeded")
				}
			}
		}()
	}
	wg.Wait()
	if n := s.Len(); n != 0 {
		t.Errorf("Len = %d; want 0", n)
	}
}
