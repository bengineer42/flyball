package front

import (
	"mime"
	"net/http"
	"strings"
	"sync"
)

// The held-connection caps per rig (D-047): websockets and GET event
// streams, each of which holds a connection to the runner for as long as
// it lasts. A runner inherits a 1024 soft descriptor limit, and at that
// limit it can no longer accept -- the UI's stop included -- nor open a
// device (a raised limit would move the failure to pyserial's select(),
// which refuses a descriptor above 1024). 512 in all leaves the rest to
// the devices, the store and the stop; callers with no credential share
// 128 of them, so a flood of anonymous viewers cannot shut out a signed-in
// operator. A POST is never counted: the stop always gets through.
const (
	maxHeldAnonymous = 128
	maxHeldTotal     = 512
)

// held counts the held connections of each rig.
type held struct {
	mu sync.Mutex
	m  map[string]*heldCount
}

type heldCount struct{ anonymous, total int }

func newHeld() *held { return &held{m: map[string]*heldCount{}} }

// acquire takes a place on rig, from the anonymous pool when anonymous; ok
// is false when the cap is reached. release gives it back.
func (h *held) acquire(rig string, anonymous bool) (release func(), ok bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	n := h.m[rig]
	if n == nil {
		n = &heldCount{}
		h.m[rig] = n
	}
	if n.total >= maxHeldTotal || (anonymous && n.anonymous >= maxHeldAnonymous) {
		return nil, false
	}
	n.total++
	if anonymous {
		n.anonymous++
	}
	var once sync.Once
	return func() {
		once.Do(func() {
			h.mu.Lock()
			defer h.mu.Unlock()
			n.total--
			if anonymous {
				n.anonymous--
			}
			if n.total == 0 {
				delete(h.m, rig)
			}
		})
	}, true
}

// holds: a request that holds a connection to the runner while it lasts --
// an upgrade, or a GET asking for an event stream (MCP's server-to-client
// stream; the runner's other GETs are answered whole).
func holds(r *http.Request) bool {
	if isUpgrade(r) {
		return true
	}
	if r.Method != http.MethodGet {
		return false
	}
	for _, v := range r.Header.Values("Accept") {
		for _, part := range strings.Split(v, ",") {
			if t, _, err := mime.ParseMediaType(strings.TrimSpace(part)); err == nil && t == "text/event-stream" {
				return true
			}
		}
	}
	return false
}

// noCredential: the anonymous visitor, or the local shape's console (no
// credential either, whoever reaches it).
func noCredential(c Caller) bool {
	return c.Scheme == SchemeAnonymous || c.Scheme == SchemeLocal
}

// refuseNoVerb answers a caller holding no verb on the rig as the runner
// would, without dialling it: no credential is 401 (a socket closed 4401,
// which the UI takes as "sign in"), anything else 403 {detail, needed}
// (4403). needed is the least verb the method could need -- read for a
// GET or a socket, operate otherwise -- the caller holding none.
func refuseNoVerb(w http.ResponseWriter, r *http.Request, c Caller) {
	needed := "operate"
	if r.Method == http.MethodGet || r.Method == http.MethodHead {
		needed = "read"
	}
	msg := "This needs '" + needed + "', which the caller does not hold here"
	switch {
	case c.Scheme == SchemeAnonymous && isUpgrade(r):
		wsRefuse(w, r, closeSignedOut, "Sign in")
	case c.Scheme == SchemeAnonymous:
		w.Header().Set("WWW-Authenticate", "Bearer")
		writeJSON(w, http.StatusUnauthorized, map[string]any{"detail": msg, "needed": needed})
	case isUpgrade(r):
		wsRefuse(w, r, closeForbidden, msg)
	default:
		writeJSON(w, http.StatusForbidden, map[string]any{"detail": msg, "needed": needed})
	}
}
