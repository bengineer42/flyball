package front

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"testing/fstest"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/principal"
)

// fakeRunner is a fronted runner as far as the front can tell: a real HTTP
// server on a real unix socket in a private temp dir, which verifies every
// request's principal with principal.Verify under its own key and aud, and
// answers the readiness handshake.
type fakeRunner struct {
	t   *testing.T
	ep  endpoint.Endpoint
	key principal.Key
	aud string

	tooOld bool // answers an unsigned probe with 200: a runner that ignores the principal
	// probeDelay holds each readiness probe this long (ns) before it is
	// answered: a runner too busy starting to answer in time.
	probeDelay atomic.Int64
	root       string // its root_path (flyballd: /<name>)

	hits   atomic.Int64 // every request that reached it, the readiness handshake included
	bodies atomic.Int64 // /mcp/body requests reading their body now

	mu       sync.Mutex
	seen     []seenRequest
	streamed chan struct{} // closed-over per stream: one value when a stream's request context ends
	wsEnded  chan struct{}
	slowDone chan struct{}
}

type seenRequest struct {
	Path    string
	Claims  principal.Claims
	Headers http.Header
	Host    string
}

// echo is what /api/echo answers.
type echo struct {
	Claims  principal.Claims `json:"claims"`
	Headers http.Header      `json:"headers"`
	Host    string           `json:"host"`
	URI     string           `json:"uri"`
}

func newFakeRunner(t *testing.T, aud string) *fakeRunner {
	t.Helper()
	dir, err := os.MkdirTemp("", "fb-front-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	fr := &fakeRunner{
		t: t, aud: aud,
		ep:       endpoint.Endpoint{Network: "unix", Address: filepath.Join(dir, "sock")},
		streamed: make(chan struct{}, 16), wsEnded: make(chan struct{}, 16), slowDone: make(chan struct{}, 16),
	}
	if _, err := rand.Read(fr.key[:]); err != nil {
		t.Fatal(err)
	}
	ln, err := net.Listen("unix", fr.ep.Address)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(fr.serve)}
	go srv.Serve(ln)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	})
	return fr
}

func (fr *fakeRunner) target() Target { return Target{Endpoint: fr.ep, Aud: fr.aud, Key: fr.key} }

func (fr *fakeRunner) requests() []seenRequest {
	fr.mu.Lock()
	defer fr.mu.Unlock()
	return slices.Clone(fr.seen)
}

func (fr *fakeRunner) serve(w http.ResponseWriter, r *http.Request) {
	fr.hits.Add(1)
	path := strings.TrimPrefix(r.URL.Path, fr.root)
	if d := time.Duration(fr.probeDelay.Load()); d > 0 && path == "/api/auth/front" {
		select {
		case <-time.After(d):
		case <-r.Context().Done():
			return
		}
	}
	if path == "/api/auth/front" {
		if fr.tooOld {
			w.Write([]byte(`{"level":"operate"}`))
			return
		}
		if len(r.Header.Values(principal.Header)) == 0 {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
	}
	tokens := r.Header.Values(principal.Header)
	var claims principal.Claims
	var err error = &principal.Error{Code: "format"}
	if len(tokens) == 1 {
		claims, err = principal.Verify(tokens[0], fr.key, fr.aud, time.Now())
	}
	if err != nil && !fr.tooOld {
		w.Header().Set("X-Flyball-Principal-Error", err.(*principal.Error).Code)
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	fr.mu.Lock()
	fr.seen = append(fr.seen, seenRequest{Path: r.URL.Path, Claims: claims, Headers: r.Header.Clone(), Host: r.Host})
	fr.mu.Unlock()

	needed := "read"
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		needed = "operate"
	}
	switch {
	case path == "/api/auth/front":
		json.NewEncoder(w).Encode(endpoint.FrontInfo{Protocol: 1, Aud: fr.aud, Pid: os.Getpid(), FlyballVersion: "test"})
	case path == "/mcp/body":
		// As FastAPI does for a JSON body (POST /mcp/read): read it all
		// before answering.
		fr.bodies.Add(1)
		io.Copy(io.Discard, r.Body)
		fr.bodies.Add(-1)
		w.Write([]byte("{}"))
	case strings.HasPrefix(path, "/api/echo") || strings.HasPrefix(path, "/mcp/"):
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(echo{Claims: claims, Headers: r.Header, Host: r.Host, URI: r.RequestURI})
	case path == "/api/guarded":
		if !slices.Contains(claims.Scp, needed) {
			w.WriteHeader(http.StatusForbidden)
			fmt.Fprintf(w, `{"detail":"forbidden","needed":%q}`, needed)
			return
		}
		w.Write([]byte(`{"ok":true}`))
	case path == "/api/bad":
		w.Header().Set("X-Flyball-Principal-Error", "mac")
		w.WriteHeader(http.StatusUnauthorized)
	case path == "/api/cookie":
		http.SetCookie(w, &http.Cookie{Name: "evil", Value: "1"})
		w.Write([]byte("ok"))
	case path == "/api/stream":
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		rc := http.NewResponseController(w)
		for i := 0; ; i++ {
			if _, err := fmt.Fprintf(w, "data: %d\n\n", i); err != nil {
				break
			}
			rc.Flush()
			select {
			case <-r.Context().Done():
			case <-time.After(50 * time.Millisecond):
				continue
			}
			break
		}
		fr.streamed <- struct{}{}
	case path == "/api/slow":
		// A short write to hardware: it finishes whatever happens to the caller.
		time.Sleep(1500 * time.Millisecond)
		w.Write([]byte("done"))
		fr.slowDone <- struct{}{}
	case path == "/ws/echo":
		if !slices.Contains(claims.Scp, "read") {
			w.WriteHeader(http.StatusForbidden)
			w.Write([]byte(`{"detail":"forbidden","needed":"read"}`))
			return
		}
		conn, brw := serverUpgrade(w, r)
		defer func() { fr.wsEnded <- struct{}{} }()
		defer conn.Close()
		writeServerFrame(conn, 0x1, []byte("hello"))
		for {
			op, payload, err := readFrame(brw.Reader)
			if err != nil {
				return
			}
			if op == 0x8 {
				writeServerFrame(conn, 0x8, payload)
				return
			}
			writeServerFrame(conn, op, payload)
		}
	case path == "/api/rig/stop":
		w.Write([]byte(`{"stopping":true}`))
	case path == "/ws/hold":
		// A socket held open until the client goes, as a dashboard's are.
		conn, brw := serverUpgrade(w, r)
		defer conn.Close()
		writeServerFrame(conn, 0x1, []byte("hello"))
		io.Copy(io.Discard, brw)
	case path == "/ws/die":
		// A runner that dies with a socket open: no close frame.
		conn, _ := serverUpgrade(w, r)
		writeServerFrame(conn, 0x1, []byte("hello"))
		conn.Close()
	case path == "/ws/kick":
		conn, _ := serverUpgrade(w, r)
		writeServerFrame(conn, 0x8, closePayload(4401, "principal refused"))
		conn.Close()
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

// serverUpgrade is a minimal RFC 6455 server handshake.
func serverUpgrade(w http.ResponseWriter, r *http.Request) (net.Conn, *bufio.ReadWriter) {
	conn, brw, err := http.NewResponseController(w).Hijack()
	if err != nil {
		panic(err)
	}
	fmt.Fprintf(brw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\nSet-Cookie: evil=1\r\n\r\n",
		wsAccept(r.Header.Get("Sec-WebSocket-Key")))
	brw.Flush()
	return conn, brw
}

func closePayload(code int, reason string) []byte {
	return append(binary.BigEndian.AppendUint16(nil, uint16(code)), reason...)
}

func writeServerFrame(w io.Writer, op byte, payload []byte) error {
	hdr := []byte{0x80 | op}
	switch n := len(payload); {
	case n < 126:
		hdr = append(hdr, byte(n))
	case n < 1<<16:
		hdr = append(hdr, 126)
		hdr = binary.BigEndian.AppendUint16(hdr, uint16(n))
	default:
		hdr = append(hdr, 127)
		hdr = binary.BigEndian.AppendUint64(hdr, uint64(n))
	}
	_, err := w.Write(append(hdr, payload...))
	return err
}

func writeClientFrame(w io.Writer, op byte, payload []byte) error {
	var mask [4]byte
	rand.Read(mask[:])
	hdr := []byte{0x80 | op}
	switch n := len(payload); {
	case n < 126:
		hdr = append(hdr, 0x80|byte(n))
	default:
		hdr = append(hdr, 0x80|126)
		hdr = binary.BigEndian.AppendUint16(hdr, uint16(n))
	}
	hdr = append(hdr, mask[:]...)
	masked := make([]byte, len(payload))
	for i, b := range payload {
		masked[i] = b ^ mask[i%4]
	}
	_, err := w.Write(append(hdr, masked...))
	return err
}

// readFrame reads one frame, unmasking a masked one.
func readFrame(r io.Reader) (op byte, payload []byte, err error) {
	var h [2]byte
	if _, err = io.ReadFull(r, h[:]); err != nil {
		return
	}
	op = h[0] & 0x0f
	n := uint64(h[1] & 0x7f)
	switch n {
	case 126:
		var ext [2]byte
		if _, err = io.ReadFull(r, ext[:]); err != nil {
			return
		}
		n = uint64(binary.BigEndian.Uint16(ext[:]))
	case 127:
		var ext [8]byte
		if _, err = io.ReadFull(r, ext[:]); err != nil {
			return
		}
		n = binary.BigEndian.Uint64(ext[:])
	}
	var mask [4]byte
	masked := h[1]&0x80 != 0
	if masked {
		if _, err = io.ReadFull(r, mask[:]); err != nil {
			return
		}
	}
	payload = make([]byte, n)
	if _, err = io.ReadFull(r, payload); err != nil {
		return
	}
	if masked {
		for i := range payload {
			payload[i] ^= mask[i%4]
		}
	}
	return
}

// wsClient is a test websocket client.
type wsClient struct {
	conn net.Conn
	br   *bufio.Reader
}

// dialWS upgrades path on the front at base (http://host:port). A non-101
// answer is returned as resp with a nil client.
func dialWS(t *testing.T, base, path string, hdr http.Header) (*wsClient, *http.Response) {
	t.Helper()
	u, _ := url.Parse(base)
	conn, err := net.Dial("tcp", u.Host)
	if err != nil {
		t.Fatal(err)
	}
	key := base64.StdEncoding.EncodeToString([]byte("0123456789abcdef"))
	var b strings.Builder
	fmt.Fprintf(&b, "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n", path, u.Host, key)
	for k, vs := range hdr {
		for _, v := range vs {
			fmt.Fprintf(&b, "%s: %s\r\n", k, v)
		}
	}
	b.WriteString("\r\n")
	if _, err := conn.Write([]byte(b.String())); err != nil {
		t.Fatal(err)
	}
	br := bufio.NewReader(conn)
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	resp, err := http.ReadResponse(br, &http.Request{Method: "GET"})
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusSwitchingProtocols {
		t.Cleanup(func() { conn.Close() })
		return nil, resp
	}
	if resp.Header.Get("Sec-WebSocket-Accept") != wsAccept(key) {
		t.Fatalf("bad Sec-WebSocket-Accept %q", resp.Header.Get("Sec-WebSocket-Accept"))
	}
	conn.SetReadDeadline(time.Time{})
	c := &wsClient{conn: conn, br: br}
	t.Cleanup(func() { conn.Close() })
	return c, resp
}

// next reads the next frame within d.
func (c *wsClient) next(d time.Duration) (byte, []byte, error) {
	c.conn.SetReadDeadline(time.Now().Add(d))
	return readFrame(c.br)
}

// closeCode reads frames until a close frame arrives within d, and returns
// its code; -1 if the connection ended without one (1006), -2 on timeout.
func (c *wsClient) closeCode(d time.Duration) (int, string) {
	deadline := time.Now().Add(d)
	for {
		c.conn.SetReadDeadline(deadline)
		op, payload, err := readFrame(c.br)
		var ne net.Error
		switch {
		case errors.As(err, &ne) && ne.Timeout():
			return -2, ""
		case err != nil:
			return -1, err.Error()
		case op == 0x8 && len(payload) >= 2:
			return int(binary.BigEndian.Uint16(payload)), string(payload[2:])
		case op == 0x8:
			return 1005, ""
		}
	}
}

// harness is a front in an httptest server over a fake runner.
type harness struct {
	t      *testing.T
	front  *Front
	srv    *httptest.Server
	runner *fakeRunner
	plan   Plan
	opts   Options
}

const testUI = "<!doctype html><title>flyball</title><div id=root></div>"

func newHarness(t *testing.T, c Config, tweak ...func(*Options)) *harness {
	t.Helper()
	fr := newFakeRunner(t, "run-0123abcd")
	plan := Resolve(c, false)
	h := &harness{t: t, runner: fr, plan: plan}
	h.opts = Options{
		Plan: plan,
		Route: SingleRig(Rig{Name: "blender", Target: func(context.Context) (Target, error) {
			return fr.target(), nil
		}}),
		TokensPath: filepath.Join(t.TempDir(), "front", "tokens.json"),
		UI:         fstest.MapFS{"index.html": {Data: []byte(testUI)}},
		FailDelay:  10 * time.Millisecond,
		Sweep:      100 * time.Millisecond,
	}
	for _, f := range tweak {
		f(&h.opts)
	}
	h.front = New(h.opts)
	h.srv = httptest.NewServer(h.front)
	t.Cleanup(func() {
		// Cut every client first: a stream a failed test left open would
		// otherwise hold srv.Close for ever.
		h.srv.CloseClientConnections()
		h.front.Close()
		h.srv.Close()
		plan.Close()
	})
	return h
}

// do sends a request to the front. body may be nil; hdr values are set as
// given (a key assigned into the map keeps its spelling on the wire).
func (h *harness) do(method, path string, body string, hdr http.Header) *http.Response {
	h.t.Helper()
	var rd io.Reader
	if body != "" {
		rd = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, h.srv.URL+path, rd)
	if err != nil {
		h.t.Fatal(err)
	}
	for k, vs := range hdr {
		req.Header[k] = vs
	}
	if body != "" && req.Header.Get("Content-Type") == "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := noRedirect.Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	h.t.Cleanup(func() { resp.Body.Close() })
	return resp
}

var noRedirect = &http.Client{
	CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	Timeout:       10 * time.Second,
}

// origin is the front's own origin, what its pages send.
func (h *harness) origin() http.Header { return http.Header{"Origin": {h.srv.URL}} }

// login signs in with the admin password and returns the session cookie.
func (h *harness) login() *http.Cookie {
	h.t.Helper()
	resp := h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, h.origin())
	if resp.StatusCode != 200 {
		b, _ := io.ReadAll(resp.Body)
		h.t.Fatalf("login: %d %s", resp.StatusCode, b)
	}
	for _, c := range resp.Cookies() {
		if c.Value != "" {
			return c
		}
	}
	h.t.Fatal("login set no cookie")
	return nil
}

func withCookie(c *http.Cookie, hdr http.Header) http.Header {
	out := hdr.Clone()
	if out == nil {
		out = http.Header{}
	}
	out["Cookie"] = []string{c.Name + "=" + c.Value}
	return out
}

func bearer(token string, hdr http.Header) http.Header {
	out := hdr.Clone()
	if out == nil {
		out = http.Header{}
	}
	out["Authorization"] = []string{"Bearer " + token}
	return out
}

func readJSON[T any](t *testing.T, resp *http.Response) T {
	t.Helper()
	var v T
	raw, _ := io.ReadAll(resp.Body)
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatalf("%d %q: %v", resp.StatusCode, raw, err)
	}
	return v
}

func body(resp *http.Response) string {
	b, _ := io.ReadAll(resp.Body)
	return string(b)
}

// rawRequest writes a request line and headers verbatim to the front and
// returns the status code, so paths reach the server exactly as written.
func (h *harness) rawRequest(requestLine string, headers ...string) int {
	h.t.Helper()
	u, _ := url.Parse(h.srv.URL)
	conn, err := net.Dial("tcp", u.Host)
	if err != nil {
		h.t.Fatal(err)
	}
	defer conn.Close()
	msg := requestLine + "\r\nHost: " + u.Host + "\r\n" + strings.Join(append(headers, ""), "\r\n") + "Connection: close\r\n\r\n"
	if _, err := conn.Write([]byte(msg)); err != nil {
		h.t.Fatal(err)
	}
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		h.t.Fatal(err)
	}
	resp.Body.Close()
	return resp.StatusCode
}

type fstestFS = fstest.MapFS

func insecureTLS() *tls.Config { return &tls.Config{InsecureSkipVerify: true} }

func netDial(addr string) (net.Conn, error) { return net.Dial("tcp", addr) }

func endpointAt(path string) endpoint.Endpoint {
	return endpoint.Endpoint{Network: "unix", Address: path}
}

func handshake(fr *fakeRunner, sign endpoint.Signer) (endpoint.FrontInfo, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return endpoint.Handshake(ctx, fr.ep, "", fr.aud, fr.key, sign)
}
