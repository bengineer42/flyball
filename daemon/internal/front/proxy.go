package front

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/httputil"
	"net/textproto"
	"slices"
	"strings"
	"time"
	"unicode"

	"flyballd/internal/backend"
	"flyballd/internal/endpoint"
	"flyballd/internal/principal"
)

// forwarded is §WP0-11's allow-list: the only request headers copied from
// the client, and only in their canonical spelling. Everything else --
// Authorization, Cookie, Origin, Referer, every X-Forwarded-* and
// Forwarded, every x-flyball-* in any spelling -- never reaches a runner.
var forwarded = map[string]bool{
	"accept": true, "accept-encoding": true, "accept-language": true, "cache-control": true,
	"content-type": true, "content-length": true, "if-match": true, "if-none-match": true,
	"if-modified-since": true, "if-unmodified-since": true, "if-range": true, "range": true,
	"user-agent": true, "last-event-id": true, "mcp-session-id": true, "mcp-protocol-version": true,
	"traceparent": true, "tracestate": true, "connection": true, "upgrade": true,
	"sec-websocket-key": true, "sec-websocket-version": true, "sec-websocket-protocol": true,
	"sec-websocket-extensions": true,
}

// forwardHeaders builds the upstream request's headers from in by the
// allow-list: a key is copied only when its lower-cased, `_`→`-` name is
// listed and the key as received is that name's canonical spelling (an
// underscore or other variant is dropped, rv-spike 3). Connection and
// Upgrade are left to the caller.
func forwardHeaders(in http.Header) http.Header {
	out := http.Header{}
	for k, v := range in {
		name := strings.ToLower(strings.ReplaceAll(k, "_", "-"))
		if !forwarded[name] || name == "connection" || name == "upgrade" ||
			k != textproto.CanonicalMIMEHeaderKey(name) {
			continue
		}
		out[k] = slices.Clone(v)
	}
	return out
}

// proxiedHeaders cleans a runner's response headers: Set-Cookie is dropped
// (the front owns every cookie), and nosniff and CSP sandbox are added so
// that nothing a runner serves can script the front's origin (F8).
func proxiedHeaders(h http.Header) {
	h.Del("Set-Cookie")
	h.Set("X-Content-Type-Options", "nosniff")
	h.Set("Content-Security-Policy", "sandbox")
}

// ProbeSigner signs the backend's readiness probe (backend.FrontOptions.Sign):
// sub front:probe, kind service, no verbs.
func ProbeSigner(now func() time.Time) endpoint.Signer {
	if now == nil {
		now = time.Now
	}
	return func(r *http.Request, key [32]byte, aud string) error {
		t := now()
		tok, err := principal.Mint(principal.Key(key), principal.Claims{
			Sub: SubProbe, Sid: "probe", Scp: []string{}, Kind: "service", Aud: aud, Sch: "http",
			Iat: t.Unix(), Exp: t.Add(principal.Lifetime).Unix(),
		})
		if err != nil {
			return err
		}
		r.Header.Set(principal.Header, tok)
		return nil
	}
}

// TargetOf is a backend channel as a Target.
func TargetOf(ch backend.Channel) (Target, error) {
	if ch.Key == ([32]byte{}) {
		return Target{}, ErrStarting // no incarnation yet
	}
	return Target{Endpoint: ch.Endpoint, Aud: ch.Aud, Key: principal.Key(ch.Key)}, nil
}

// StatusErr is the error a Rig.Target returns for a backend status: nil
// for running, ErrStarting while it starts or restarts, ErrNotRunning
// otherwise.
func StatusErr(st backend.Status) error {
	switch st {
	case backend.StatusRunning:
		return nil
	case backend.StatusStarting, backend.StatusRestarting:
		return ErrStarting
	}
	return fmt.Errorf("%w (%s)", ErrNotRunning, st)
}

// target is rig's runner, proxyable: a key whose handshake has not passed
// is checked first (unsigned 401, signed 200), so a runner too old to
// enforce the principal is never proxied to (merge requirement 27).
func (f *Front) target(ctx context.Context, rig Rig) (Target, error) {
	if rig.Target == nil {
		return Target{}, ErrNotRunning
	}
	t, err := rig.Target(ctx)
	if err != nil {
		return Target{}, err
	}
	id := t.Endpoint.String() + "|" + t.Aud
	f.mu.Lock()
	ok := f.verified[id] == [32]byte(t.Key)
	f.mu.Unlock()
	if ok {
		return t, nil
	}
	hctx, cancel := context.WithTimeout(ctx, 2*endpoint.ProbeTimeout)
	defer cancel()
	if _, err := endpoint.Handshake(hctx, t.Endpoint, rig.Root, t.Aud, t.Key, f.signer); err != nil {
		if errors.Is(err, endpoint.ErrNotListening) {
			return Target{}, fmt.Errorf("%w: %v", ErrStarting, err)
		}
		f.log.Error("front: refusing to proxy", "rig", rig.Name, "err", err)
		return Target{}, fmt.Errorf("%w: %v", ErrTooOld, err)
	}
	f.mu.Lock()
	f.verified[id] = t.Key
	f.mu.Unlock()
	return t, nil
}

// unverify forgets a passed handshake (the runner refused a principal).
func (f *Front) unverify(t Target) {
	f.mu.Lock()
	delete(f.verified, t.Endpoint.String()+"|"+t.Aud)
	f.mu.Unlock()
}

func (f *Front) transport(e endpoint.Endpoint) *http.Transport {
	f.mu.Lock()
	defer f.mu.Unlock()
	tr, ok := f.transports[e.String()]
	if !ok {
		tr = e.Transport()
		f.transports[e.String()] = tr
	}
	return tr
}

func targetError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrStarting):
		w.Header().Set("Retry-After", "1")
		detail(w, http.StatusServiceUnavailable, "The rig's runner is starting")
	case errors.Is(err, ErrTooOld):
		detail(w, http.StatusBadGateway, "The rig's runner is too old for this front")
	case errors.Is(err, ErrNotRunning):
		detail(w, http.StatusServiceUnavailable, "The rig's runner is not running")
	default:
		detail(w, http.StatusBadGateway, "The rig's runner cannot be reached")
	}
}

// claims is the principal for c on rig at t.
func (f *Front) claims(c Caller, rig Rig, t Target, r *http.Request) principal.Claims {
	now := f.now()
	return principal.Claims{
		Sub: c.Sub, Nm: printable(c.Name), Sid: c.Sid, Scp: f.Verbs(c, rig.Name), Kind: c.Kind,
		Aud: t.Aud, Cip: f.peer(r), Sch: f.scheme(r),
		Iat: now.Unix(), Exp: now.Add(principal.Lifetime).Unix(),
	}
}

// printable drops what a principal may not carry (control characters,
// U+2028/9) from a display name.
func printable(s string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsControl(r) || r == '\u2028' || r == '\u2029' {
			return -1
		}
		return r
	}, s)
}

var errOutOfStep = errors.New("front and runner out of step")

// serveProxy sends one request under a rig's /api, /ws or /mcp to its
// runner with a fresh principal.
func (f *Front) serveProxy(w http.ResponseWriter, r *http.Request, rig Rig) {
	c, err := f.authenticate(r, false)
	if err != nil {
		f.refuse(w, r, err)
		return
	}
	if c.Scheme != SchemeToken && acts(r) && !f.CheckOrigin(r) {
		detail(w, http.StatusForbidden, "Origin not allowed")
		return
	}
	t, err := f.target(r.Context(), rig)
	if err != nil {
		targetError(w, err)
		return
	}
	tok, err := principal.Mint(t.Key, f.claims(c, rig, t, r))
	if err != nil {
		f.log.Error("front: minting a principal", "sub", c.Sub, "err", err)
		detail(w, http.StatusInternalServerError, "Internal error")
		return
	}
	id := randomHex(16)
	if isUpgrade(r) {
		f.proxyUpgrade(w, r, c, t, tok, id)
		return
	}

	ctx := r.Context()
	// Reads and streams are cut when the credential ends; a write already
	// on its way to hardware is not (C12).
	if r.Method == http.MethodGet || r.Method == http.MethodHead {
		var cancel context.CancelFunc
		ctx, cancel = context.WithCancel(ctx)
		defer cancel()
		remove := f.cancels.add(c.Sid, func(int, string) { cancel() })
		defer remove() // runs through ReverseProxy's http.ErrAbortHandler panic
	}
	rp := &httputil.ReverseProxy{
		Transport:     f.transport(t.Endpoint),
		FlushInterval: -1,
		Rewrite: func(pr *httputil.ProxyRequest) {
			out := pr.Out
			out.URL.Scheme, out.URL.Host = "http", "localhost"
			out.Host = "localhost"
			// Out.Header has already lost the hop-by-hop headers (and any
			// named in Connection); the allow-list is applied to what is left.
			out.Header = forwardHeaders(out.Header)
			out.Header.Set(principal.Header, tok)
			out.Header.Set("X-Request-Id", id)
		},
		ModifyResponse: func(resp *http.Response) error {
			proxiedHeaders(resp.Header)
			resp.Header.Set("X-Request-Id", id)
			switch {
			case resp.StatusCode == http.StatusUnauthorized:
				f.unverify(t)
				f.log.Error("front: the runner refused a principal", "code", resp.Header.Get("X-Flyball-Principal-Error"),
					"path", r.URL.Path, "request", id)
				return errOutOfStep
			case resp.StatusCode == http.StatusForbidden && c.Scheme == SchemeAnonymous:
				resp.StatusCode, resp.Status = http.StatusUnauthorized, "401 Unauthorized"
				resp.Header.Set("WWW-Authenticate", "Bearer")
			}
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, pr *http.Request, err error) {
			switch {
			case errors.Is(err, errOutOfStep):
				detail(w, http.StatusBadGateway, "The front and the rig's runner are out of step")
			case pr.Context().Err() != nil: // revoked, or the client went away
			case endpoint.NotListening(err):
				f.unverify(t)
				w.Header().Set("Retry-After", "1")
				detail(w, http.StatusServiceUnavailable, "The rig's runner is starting")
			default:
				f.log.Error("front: proxy", "path", pr.URL.Path, "request", id, "err", err)
				detail(w, http.StatusBadGateway, "The rig's runner cannot be reached")
			}
		},
		// ReverseProxy's own lines (a body copy cut by a revocation) are
		// noise; the ErrorHandler logs what matters, without query strings.
		ErrorLog: log.New(io.Discard, "", 0),
	}
	rp.ServeHTTP(w, r.WithContext(ctx))
}
