package front

import "sync"

// cancels is the revocation registry: the live websockets and long
// streams of each credential, keyed by the sid the principal carries (a
// session's sid, a token's id). Logout, revocation and expiry end them
// (merge requirement 11). Only upgraded connections and GET/HEAD streams
// are registered: a short write on its way to hardware is never cut
// (rv-codebase C12).
type cancels struct {
	mu   sync.Mutex
	next uint64
	m    map[string]map[uint64]func(code int, reason string)
}

func newCancels() *cancels {
	return &cancels{m: map[string]map[uint64]func(int, string){}}
}

// add registers end under sid and returns its removal, which the caller
// defers: cleanup survives ReverseProxy's http.ErrAbortHandler panic.
func (c *cancels) add(sid string, end func(code int, reason string)) (remove func()) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.next++
	id := c.next
	if c.m[sid] == nil {
		c.m[sid] = map[uint64]func(int, string){}
	}
	c.m[sid][id] = end
	return func() {
		c.mu.Lock()
		defer c.mu.Unlock()
		delete(c.m[sid], id)
		if len(c.m[sid]) == 0 {
			delete(c.m, sid)
		}
	}
}

// end ends everything registered under sid (4401: signed out).
func (c *cancels) end(sid string) {
	c.endWhere(func(s string) bool { return s == sid }, closeSignedOut, "signed out")
}

// endAll ends everything (the front is shutting down: 1001, going away).
func (c *cancels) endAll() {
	c.endWhere(func(string) bool { return true }, closeGoingAway, "front shutting down")
}

func (c *cancels) endWhere(match func(string) bool, code int, reason string) {
	var fns []func(int, string)
	c.mu.Lock()
	for sid, m := range c.m {
		if !match(sid) {
			continue
		}
		for _, fn := range m {
			fns = append(fns, fn)
		}
		delete(c.m, sid)
	}
	c.mu.Unlock()
	for _, fn := range fns {
		fn(code, reason)
	}
}

func (c *cancels) len() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	n := 0
	for _, m := range c.m {
		n += len(m)
	}
	return n
}
