package front

import (
	"bufio"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"flyballd/internal/principal"
)

// Websocket close codes the front sends.
const (
	closeGoingAway  = 1001 // the front is shutting down
	closeTryLater   = 1013 // over a held-connection cap (caps.go): the UI retries with backoff
	closeBadGateway = 1014 // the runner refused the front's principal: out of step, not signed out
	closeSignedOut  = 4401 // the credential is refused, revoked or expired: the UI stops retrying
	closeForbidden  = 4403 // a signed-in caller lacking the verb, as the runner closes it
)

// lockWait bounds how long a revocation waits for a frame in flight to the
// client before it closes the connection without a close frame.
const lockWait = 250 * time.Millisecond

// drainWait is how long, after its close frame, the front keeps reading
// from the client, so that the client reads the frame before the TCP
// connection goes (an unread receive buffer would turn the close into a
// reset that can discard it).
const drainWait = time.Second

// wsAccept is RFC 6455's Sec-WebSocket-Accept.
func wsAccept(key string) string {
	h := sha1.Sum([]byte(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	return base64.StdEncoding.EncodeToString(h[:])
}

func closeFrame(code int, reason string) []byte {
	if len(reason) > 123 {
		reason = reason[:123]
	}
	p := binary.BigEndian.AppendUint16(nil, uint16(code))
	p = append(p, reason...)
	return append([]byte{0x88, byte(len(p))}, p...)
}

// wsRefuse refuses an upgrade the websocket way (merge requirement 26):
// the handshake completes and the connection closes with code and reason,
// so the client sees a close frame -- not an HTTP 403 it cannot read, nor
// a 1006 it would retry for ever.
func wsRefuse(w http.ResponseWriter, r *http.Request, code int, reason string) {
	key := r.Header.Get("Sec-WebSocket-Key")
	if key == "" || r.Header.Get("Sec-WebSocket-Version") != "13" {
		detail(w, http.StatusUnauthorized, reason)
		return
	}
	conn, brw, err := http.NewResponseController(w).Hijack()
	if err != nil {
		detail(w, http.StatusUnauthorized, reason)
		return
	}
	fmt.Fprintf(brw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n", wsAccept(key))
	for _, c := range w.Header().Values("Set-Cookie") { // a cleared cookie
		fmt.Fprintf(brw, "Set-Cookie: %s\r\n", c)
	}
	brw.WriteString("\r\n")
	brw.Write(closeFrame(code, reason))
	brw.Flush()
	go linger(conn, brw.Reader)
}

// linger half-closes conn, reads what the client still sends for up to
// drainWait, then closes it.
func linger(conn net.Conn, r io.Reader) {
	if cw, ok := conn.(interface{ CloseWrite() error }); ok {
		cw.CloseWrite()
	}
	conn.SetReadDeadline(time.Now().Add(drainWait))
	io.Copy(io.Discard, r)
	conn.Close()
}

// proxyUpgrade carries a websocket to the runner. It is done here rather
// than by ReverseProxy so that the front can close the client's side with
// a real close frame when the credential ends (4401), and so that it sees
// the runner's close codes.
func (f *Front) proxyUpgrade(w http.ResponseWriter, r *http.Request, c Caller, t Target, tok, id string) {
	if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		detail(w, http.StatusBadRequest, "Only websocket upgrades are proxied")
		return
	}
	out := (&http.Request{
		Method: http.MethodGet,
		URL:    &url.URL{Scheme: "http", Host: "localhost", Path: r.URL.Path, RawPath: r.URL.RawPath, RawQuery: r.URL.RawQuery},
		Proto:  "HTTP/1.1", ProtoMajor: 1, ProtoMinor: 1,
		Header: forwardHeaders(r.Header),
		Host:   "localhost",
	}).WithContext(r.Context())
	out.Header.Set("Connection", "Upgrade")
	out.Header.Set("Upgrade", "websocket")
	out.Header.Set(principal.Header, tok)
	out.Header.Set("X-Request-Id", id)

	resp, err := f.transport(t.Endpoint).RoundTrip(out)
	if err != nil {
		f.log.Error("front: websocket to the runner", "path", r.URL.Path, "request", id, "err", err)
		detail(w, http.StatusBadGateway, "The rig's runner cannot be reached")
		return
	}
	if resp.StatusCode != http.StatusSwitchingProtocols {
		defer resp.Body.Close()
		switch {
		case resp.StatusCode == http.StatusUnauthorized:
			f.unverify(t)
			f.log.Error("front: the runner refused a principal", "code", resp.Header.Get("X-Flyball-Principal-Error"), "request", id)
			detail(w, http.StatusBadGateway, "The front and the rig's runner are out of step")
		case resp.StatusCode == http.StatusForbidden && c.Scheme == SchemeAnonymous:
			wsRefuse(w, r, closeSignedOut, "Sign in")
		default:
			for k, v := range resp.Header {
				w.Header()[k] = v
			}
			proxiedHeaders(w.Header())
			w.WriteHeader(resp.StatusCode)
			io.Copy(w, io.LimitReader(resp.Body, 1<<20))
		}
		return
	}
	back, ok := resp.Body.(io.ReadWriteCloser)
	if !ok {
		resp.Body.Close()
		detail(w, http.StatusBadGateway, "The rig's runner cannot be reached")
		return
	}
	conn, brw, err := http.NewResponseController(w).Hijack()
	if err != nil {
		back.Close()
		return
	}
	fmt.Fprintf(brw, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nX-Request-Id: %s\r\n", id)
	for _, k := range []string{"Sec-Websocket-Accept", "Sec-Websocket-Protocol", "Sec-Websocket-Extensions"} {
		for _, v := range resp.Header.Values(k) {
			fmt.Fprintf(brw, "%s: %s\r\n", k, v)
		}
	}
	brw.WriteString("\r\n")
	if err := brw.Flush(); err != nil {
		conn.Close()
		back.Close()
		return
	}
	b := &wsBridge{client: conn, clientR: brw.Reader, back: back, lock: make(chan struct{}, 1), closing: make(chan struct{})}
	remove := f.cancels.add(c.Sid, b.end)
	defer remove()
	b.run()
}

// wsBridge copies one websocket both ways: client → runner as bytes, and
// runner → client frame by frame, so that a close frame can be written
// between two frames.
type wsBridge struct {
	client  net.Conn
	clientR io.Reader
	back    io.ReadWriteCloser

	lock    chan struct{} // held while a frame is written to the client
	closing chan struct{}
	once    sync.Once
	ended   atomic.Bool
}

func (b *wsBridge) run() {
	done := make(chan struct{}, 2)
	go func() { b.fromClient(); done <- struct{}{} }()
	go func() { b.fromRunner(); done <- struct{}{} }()
	<-done
	// One side has ended: end the runner's side, and give the client a
	// moment to read what was sent before its side closes.
	b.shut()
	<-done
	b.client.Close()
}

func (b *wsBridge) shut() {
	b.once.Do(func() {
		close(b.closing)
		b.back.Close()
		b.client.SetReadDeadline(time.Now().Add(drainWait))
	})
}

// end is the revocation: a close frame with code to the client, after any
// frame in flight, then the runner's side is closed.
func (b *wsBridge) end(code int, reason string) {
	if b.ended.Swap(true) {
		return
	}
	select {
	case b.lock <- struct{}{}: // never released: nothing more is written
		b.client.SetWriteDeadline(time.Now().Add(lockWait))
		b.client.Write(closeFrame(code, reason))
		if cw, ok := b.client.(interface{ CloseWrite() error }); ok {
			cw.CloseWrite()
		}
	case <-time.After(lockWait):
		b.client.Close() // a frame is stuck mid-write: 1006 is all that is left
	}
	b.shut()
}

func (b *wsBridge) fromClient() {
	buf := make([]byte, 32<<10)
	discard := false
	for {
		n, err := b.clientR.Read(buf)
		if n > 0 && !discard {
			if _, werr := b.back.Write(buf[:n]); werr != nil {
				discard = true // keep reading until the client closes or the drain ends
			}
		}
		if err != nil {
			return
		}
	}
}

func (b *wsBridge) fromRunner() {
	br := bufio.NewReader(b.back)
	for {
		hdr, n, err := readFrameHeader(br)
		if err != nil {
			return
		}
		op, masked := hdr[0]&0x0f, hdr[1]&0x80 != 0
		if op == 0x8 && !masked && n <= 125 {
			payload := make([]byte, n)
			if _, err := io.ReadFull(br, payload); err != nil {
				return
			}
			frame := append(hdr, payload...)
			if n >= 2 && binary.BigEndian.Uint16(payload) == closeSignedOut {
				// The runner refused the principal it was given: the caller
				// is still signed in at the front (F18).
				frame = closeFrame(closeBadGateway, "front and runner out of step")
			}
			b.write(frame, nil, 0)
			return
		}
		if !b.write(hdr, br, n) {
			return
		}
	}
}

// write sends hdr, then n bytes of payload from r, to the client under the
// frame lock; false once the bridge is ending.
func (b *wsBridge) write(hdr []byte, r io.Reader, n uint64) bool {
	select {
	case b.lock <- struct{}{}:
	case <-b.closing:
		return false
	}
	defer func() { <-b.lock }()
	if b.ended.Load() {
		return false
	}
	if _, err := b.client.Write(hdr); err != nil {
		return false
	}
	if r != nil && n > 0 {
		if _, err := io.CopyN(b.client, r, int64(n)); err != nil {
			return false
		}
	}
	return true
}

// readFrameHeader reads one frame's header (the mask key included) and
// returns it with the payload length.
func readFrameHeader(r *bufio.Reader) ([]byte, uint64, error) {
	hdr := make([]byte, 2, 14)
	if _, err := io.ReadFull(r, hdr); err != nil {
		return nil, 0, err
	}
	n := uint64(hdr[1] & 0x7f)
	switch n {
	case 126:
		ext := make([]byte, 2)
		if _, err := io.ReadFull(r, ext); err != nil {
			return nil, 0, err
		}
		hdr = append(hdr, ext...)
		n = uint64(binary.BigEndian.Uint16(ext))
	case 127:
		ext := make([]byte, 8)
		if _, err := io.ReadFull(r, ext); err != nil {
			return nil, 0, err
		}
		hdr = append(hdr, ext...)
		n = binary.BigEndian.Uint64(ext)
		if n > 1<<62 {
			return nil, 0, errors.New("websocket frame too long")
		}
	}
	if hdr[1]&0x80 != 0 {
		mask := make([]byte, 4)
		if _, err := io.ReadFull(r, mask); err != nil {
			return nil, 0, err
		}
		hdr = append(hdr, mask...)
	}
	return hdr, n, nil
}
