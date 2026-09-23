package store

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

// fakeClock is a hand-moved clock for the limiter and the sessions.
type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newFakeClock() *fakeClock {
	return &fakeClock{t: time.Date(2026, 9, 23, 12, 0, 0, 0, time.UTC)}
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

func TestPeerKey(t *testing.T) {
	for in, want := range map[string]string{
		"192.0.2.7:51234":                    "192.0.2.7",
		"192.0.2.7":                          "192.0.2.7",
		"[2001:db8:1:2:aaaa::1]:443":         "2001:db8:1:2::/64",
		"[2001:db8:1:2:bbbb:cccc:dddd:eeee]": "2001:db8:1:2::/64",
		"2001:db8:1:3::1":                    "2001:db8:1:3::/64",
		"[::ffff:192.0.2.7]:80":              "192.0.2.7",
		"@":                                  "@",
		"":                                   "",
	} {
		if got := PeerKey(in); got != want {
			t.Errorf("PeerKey(%q) = %q; want %q", in, got, want)
		}
	}
}

func TestLimiter(t *testing.T) {
	clock := newFakeClock()
	l := NewLimiter(LoginAttempts, LoginWindow, LimiterEntries, clock.Now)
	peer := "192.0.2.7:1000"
	for i := range LoginAttempts {
		if ok, _ := l.Allow(peer); !ok {
			t.Fatalf("attempt %d refused; want %d allowed", i+1, LoginAttempts)
		}
		l.Failure(peer)
		clock.Advance(time.Second)
	}
	ok, wait := l.Allow(peer)
	if ok {
		t.Fatalf("attempt %d allowed; want the peer held off", LoginAttempts+1)
	}
	// The first failure was at t0; now is t0+10 s; it leaves the window at t0+60 s.
	if want := LoginWindow - LoginAttempts*time.Second; wait != want {
		t.Errorf("retry after %v; want %v", wait, want)
	}
	if ok, _ := l.Allow("192.0.2.8:1000"); !ok {
		t.Errorf("another peer refused; the limit is per peer")
	}
	if ok, _ := l.Allow("192.0.2.7:2000"); ok {
		t.Errorf("same address, another port allowed; the port is not the peer")
	}
	clock.Advance(wait)
	if ok, _ := l.Allow(peer); !ok {
		t.Errorf("still refused after the oldest failure left the window: a delay, not a ban")
	}
}

func TestLimiterIPv6Per64(t *testing.T) {
	clock := newFakeClock()
	l := NewLimiter(3, time.Minute, 100, clock.Now)
	for i := range 3 {
		l.Failure(fmt.Sprintf("[2001:db8:0:1::%x]:443", i+1)) // three addresses, one /64
	}
	if ok, _ := l.Allow("[2001:db8:0:1:ffff::9]:443"); ok {
		t.Errorf("a fourth address in the same /64 allowed; want the /64 held off")
	}
	if ok, _ := l.Allow("[2001:db8:0:2::1]:443"); !ok {
		t.Errorf("the neighbouring /64 refused")
	}
}

func TestLimiterEviction(t *testing.T) {
	clock := newFakeClock()
	const max = 10_000
	l := NewLimiter(LoginAttempts, LoginWindow, max, clock.Now)
	for range LoginAttempts {
		l.Failure("198.51.100.1")
	}
	for i := range 3 * max {
		l.Failure(fmt.Sprintf("10.%d.%d.%d", i>>16&0xff, i>>8&0xff, i&0xff))
		if n := l.Len(); n > max {
			t.Fatalf("after %d peers the limiter holds %d entries; want at most %d", i+1, n, max)
		}
	}
	if n := l.Len(); n != max {
		t.Errorf("Len = %d; want %d", n, max)
	}
	// The least recently failed peer was the first evicted.
	if ok, _ := l.Allow("198.51.100.1"); !ok {
		t.Errorf("the oldest peer still held off after %d newer peers; want it evicted", 3*max)
	}
	// Expired entries go first: after the window, the map drains as it is used.
	clock.Advance(LoginWindow + time.Second)
	l.Failure("203.0.113.1")
	if n := l.Len(); n != 1 {
		t.Errorf("Len after the window passed = %d; want 1 (expired entries pruned)", n)
	}
}

func TestLimiterConcurrent(t *testing.T) {
	l := NewLimiter(LoginAttempts, LoginWindow, 64, time.Now)
	var wg sync.WaitGroup
	for g := range 16 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range 500 {
				peer := fmt.Sprintf("192.0.2.%d:%d", (g*7+i)%200, i)
				l.Allow(peer)
				l.Failure(peer)
			}
		}()
	}
	wg.Wait()
	if n := l.Len(); n > 64 {
		t.Errorf("Len = %d; want at most 64", n)
	}
}
