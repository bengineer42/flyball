package store

import (
	"container/list"
	"net"
	"net/netip"
	"strings"
	"sync"
	"time"
)

// Limiter holds off a peer that has failed too many logins recently: at
// most `limit` failures within `window`, after which Allow says no until the
// oldest of them leaves the window. It delays, and never bans. The front asks
// it before hashing, so a refused attempt costs no scrypt.
//
// Memory is bounded: at most `max` peers are remembered, and past that the
// peer whose last failure is oldest is forgotten (so a flood of fresh
// addresses can wash out an entry; the hashing semaphore is the backstop).
// Peers whose last failure has left the window are pruned as the limiter is
// used. It is safe for concurrent use.
type Limiter struct {
	limit  int
	window time.Duration
	max    int
	now    func() time.Time

	mu    sync.Mutex
	peers map[string]*list.Element // of *peerFailures
	order *list.List               // front: the most recent failure
}

type peerFailures struct {
	key   string
	times []time.Time // oldest first; at most limit of them
}

// NewLimiter returns a Limiter (in the front: LoginAttempts, LoginWindow,
// LimiterEntries). now is the clock; nil means time.Now, whose readings carry
// Go's monotonic clock, so a wall-clock step (a Pi finding NTP) moves nothing.
func NewLimiter(limit int, window time.Duration, max int, now func() time.Time) *Limiter {
	if now == nil {
		now = time.Now
	}
	return &Limiter{
		limit: max1(limit), window: window, max: max1(max), now: now,
		peers: map[string]*list.Element{}, order: list.New(),
	}
}

func max1(n int) int {
	if n < 1 {
		return 1
	}
	return n
}

// PeerKey is the limiter's key for a peer address, `host:port` or a bare
// host: an IPv4 address as itself (an IPv4-mapped IPv6 one included), an
// IPv6 address as its /64 (one host usually holds the whole /64), and
// anything that is not an IP (a unix socket's "@" or "") as given.
func PeerKey(peer string) string {
	host := peer
	if h, _, err := net.SplitHostPort(peer); err == nil {
		host = h
	}
	addr, err := netip.ParseAddr(strings.TrimSuffix(strings.TrimPrefix(host, "["), "]"))
	if err != nil {
		return peer
	}
	addr = addr.Unmap()
	if addr.Is4() {
		return addr.String()
	}
	prefix, err := addr.WithZone("").Prefix(64)
	if err != nil {
		return peer
	}
	return prefix.String()
}

// Allow reports whether peer may try a password now; when not, how long
// until it may (the front's Retry-After).
func (l *Limiter) Allow(peer string) (bool, time.Duration) {
	now := l.now()
	l.mu.Lock()
	defer l.mu.Unlock()
	el, ok := l.peers[PeerKey(peer)]
	if !ok {
		return true, 0
	}
	p := el.Value.(*peerFailures)
	p.times = l.recent(p.times, now)
	if len(p.times) < l.limit {
		return true, 0
	}
	return false, p.times[0].Add(l.window).Sub(now)
}

// Failure records a wrong password from peer.
func (l *Limiter) Failure(peer string) {
	now := l.now()
	key := PeerKey(peer)
	l.mu.Lock()
	defer l.mu.Unlock()
	l.prune(now)
	el, ok := l.peers[key]
	if !ok {
		el = l.order.PushFront(&peerFailures{key: key})
		l.peers[key] = el
	}
	p := el.Value.(*peerFailures)
	p.times = append(l.recent(p.times, now), now)
	if len(p.times) > l.limit {
		p.times = append(p.times[:0], p.times[len(p.times)-l.limit:]...)
	}
	l.order.MoveToFront(el)
	for len(l.peers) > l.max {
		l.remove(l.order.Back())
	}
}

// Len is how many peers the limiter remembers.
func (l *Limiter) Len() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.peers)
}

// recent drops the failures that have left the window.
func (l *Limiter) recent(times []time.Time, now time.Time) []time.Time {
	i := 0
	for i < len(times) && now.Sub(times[i]) >= l.window {
		i++
	}
	return times[i:]
}

// prune forgets, from the oldest end, every peer whose last failure has left
// the window.
func (l *Limiter) prune(now time.Time) {
	for el := l.order.Back(); el != nil; el = l.order.Back() {
		p := el.Value.(*peerFailures)
		if len(p.times) > 0 && now.Sub(p.times[len(p.times)-1]) < l.window {
			return
		}
		l.remove(el)
	}
}

func (l *Limiter) remove(el *list.Element) {
	delete(l.peers, el.Value.(*peerFailures).key)
	l.order.Remove(el)
}
