// Package wsclient is a minimal RFC 6455 client, just enough to read
// text frames from a runner's /ws/<stream> endpoints (flyball watch).
// No external dependency is pulled in for this -- the frame shape needed
// is narrow (server->client text frames only, no fragmentation handling
// beyond the common case), so a small hand-rolled client is cheaper than
// a new go.mod dependency for one command.
package wsclient

import (
	"bufio"
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
)

const magicGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

// maxFrameLength caps a single frame's payload; a runner has no reason to
// send anything close to this over /ws/<stream>, and an unbounded length
// (up to 2^63-1 per RFC 6455) would otherwise let a frame header alone
// commit the client to an arbitrarily large allocation and read.
const maxFrameLength = 16 * 1024 * 1024

// Conn is an open websocket connection, text-frame reads only.
type Conn struct {
	rw  io.ReadWriteCloser
	buf *bufio.Reader
}

// Dial performs the HTTP Upgrade handshake against a ws:// or wss:// URL
// and returns a Conn ready for ReadMessage. TLS (wss) is not implemented
// -- flyball runners are loopback/plain HTTP today, per plan.md.
func Dial(rawURL string, headers http.Header) (*Conn, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	if u.Scheme != "ws" {
		return nil, fmt.Errorf("wsclient: unsupported scheme %q (only ws://)", u.Scheme)
	}
	host := u.Host
	if !strings.Contains(host, ":") {
		host += ":80"
	}
	conn, err := net.Dial("tcp", host)
	if err != nil {
		return nil, err
	}

	keyRaw := make([]byte, 16)
	if _, err := rand.Read(keyRaw); err != nil {
		conn.Close()
		return nil, err
	}
	key := base64.StdEncoding.EncodeToString(keyRaw)

	path := u.Path
	if u.RawQuery != "" {
		path += "?" + u.RawQuery
	}
	var reqBuf strings.Builder
	fmt.Fprintf(&reqBuf, "GET %s HTTP/1.1\r\n", path)
	fmt.Fprintf(&reqBuf, "Host: %s\r\n", u.Host)
	reqBuf.WriteString("Upgrade: websocket\r\n")
	reqBuf.WriteString("Connection: Upgrade\r\n")
	fmt.Fprintf(&reqBuf, "Sec-WebSocket-Key: %s\r\n", key)
	reqBuf.WriteString("Sec-WebSocket-Version: 13\r\n")
	for name, values := range headers {
		for _, v := range values {
			fmt.Fprintf(&reqBuf, "%s: %s\r\n", name, v)
		}
	}
	reqBuf.WriteString("\r\n")

	if _, err := conn.Write([]byte(reqBuf.String())); err != nil {
		conn.Close()
		return nil, err
	}

	br := bufio.NewReader(conn)
	resp, err := http.ReadResponse(br, &http.Request{Method: "GET"})
	if err != nil {
		conn.Close()
		return nil, err
	}
	if resp.StatusCode != http.StatusSwitchingProtocols {
		conn.Close()
		return nil, fmt.Errorf("wsclient: handshake failed: %s", resp.Status)
	}
	expected := acceptKey(key)
	if resp.Header.Get("Sec-WebSocket-Accept") != expected {
		conn.Close()
		return nil, fmt.Errorf("wsclient: bad Sec-WebSocket-Accept")
	}

	return &Conn{rw: conn, buf: br}, nil
}

func acceptKey(key string) string {
	h := sha1.New()
	h.Write([]byte(key + magicGUID))
	return base64.StdEncoding.EncodeToString(h.Sum(nil))
}

// ReadMessage returns the next text message's payload, reassembling
// fragmented frames. Ping/pong/close are handled transparently; a close
// frame returns io.EOF.
func (c *Conn) ReadMessage() ([]byte, error) {
	for {
		fin, opcode, payload, err := c.readFrame()
		if err != nil {
			return nil, err
		}
		switch opcode {
		case 0x8: // close
			return nil, io.EOF
		case 0x9: // ping -- reply pong, keep reading
			c.writeFrame(0xA, payload)
			continue
		case 0xA: // pong
			continue
		case 0x1, 0x0: // text or continuation
			if !fin {
				rest, err := c.ReadMessage()
				if err != nil {
					return nil, err
				}
				return append(payload, rest...), nil
			}
			return payload, nil
		default:
			continue
		}
	}
}

func (c *Conn) readFrame() (fin bool, opcode byte, payload []byte, err error) {
	head := make([]byte, 2)
	if _, err = io.ReadFull(c.buf, head); err != nil {
		return
	}
	fin = head[0]&0x80 != 0
	if head[0]&0x70 != 0 {
		err = fmt.Errorf("wsclient: reserved bits set (no extension negotiated)")
		return
	}
	opcode = head[0] & 0x0F
	masked := head[1]&0x80 != 0
	if masked {
		err = fmt.Errorf("wsclient: server frame is masked (RFC 6455 forbids this)")
		return
	}
	length := uint64(head[1] & 0x7F)

	switch length {
	case 126:
		ext := make([]byte, 2)
		if _, err = io.ReadFull(c.buf, ext); err != nil {
			return
		}
		length = uint64(binary.BigEndian.Uint16(ext))
	case 127:
		ext := make([]byte, 8)
		if _, err = io.ReadFull(c.buf, ext); err != nil {
			return
		}
		length = binary.BigEndian.Uint64(ext)
	}
	if length > maxFrameLength {
		err = fmt.Errorf("wsclient: frame too large (%d bytes, cap is %d)", length, maxFrameLength)
		return
	}

	payload = make([]byte, length)
	if _, err = io.ReadFull(c.buf, payload); err != nil {
		return
	}
	return
}

// writeFrame sends a client->server frame; client frames must be masked
// per RFC 6455.
func (c *Conn) writeFrame(opcode byte, payload []byte) error {
	var mask [4]byte
	rand.Read(mask[:])
	masked := make([]byte, len(payload))
	for i, b := range payload {
		masked[i] = b ^ mask[i%4]
	}

	var out []byte
	out = append(out, 0x80|opcode)
	n := len(payload)
	switch {
	case n < 126:
		out = append(out, 0x80|byte(n))
	case n < 65536:
		out = append(out, 0x80|126)
		ext := make([]byte, 2)
		binary.BigEndian.PutUint16(ext, uint16(n))
		out = append(out, ext...)
	default:
		out = append(out, 0x80|127)
		ext := make([]byte, 8)
		binary.BigEndian.PutUint64(ext, uint64(n))
		out = append(out, ext...)
	}
	out = append(out, mask[:]...)
	out = append(out, masked...)
	_, err := c.rw.Write(out)
	return err
}

func (c *Conn) Close() error { return c.rw.Close() }
