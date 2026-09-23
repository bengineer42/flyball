package wsclient

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"strings"
	"testing"
)

// newConn builds a Conn around raw frame bytes, for exercising readFrame
// directly without a real socket.
func newConn(raw []byte) *Conn {
	return &Conn{buf: bufio.NewReader(bytes.NewReader(raw))}
}

// TestReadFrameRejectsOversizedLength feeds a frame header declaring a
// payload bigger than the 16 MiB cap and checks it is rejected without
// the reader trying to consume that much data (the raw buffer here holds
// only the header, so a read attempt would fail with EOF instead of the
// intended "frame too large" error if the cap were missing).
func TestReadFrameRejectsOversizedLength(t *testing.T) {
	var head [2]byte
	head[0] = 0x80 | 0x1 // fin, text
	head[1] = 127        // 8-byte extended length follows, unmasked
	var ext [8]byte
	binary.BigEndian.PutUint64(ext[:], 16*1024*1024+1) // one byte over the cap

	raw := append(head[:], ext[:]...)
	c := newConn(raw)

	_, _, _, err := c.readFrame()
	if err == nil {
		t.Fatal("expected an error for an oversized frame, got nil")
	}
	// The rejection must come from the length check itself, not from the
	// reader running out of the (deliberately short) fake payload: that
	// would also produce a non-nil error and let this test pass for the
	// wrong reason.
	if !strings.Contains(err.Error(), "too large") {
		t.Fatalf("error = %q, want a message about the frame being too large", err)
	}
}

// TestReadFrameRejectsMaskedServerFrame feeds a well-formed but masked
// frame, as if a server violated RFC 6455 (server->client frames must
// not be masked). It must be rejected, not silently unmasked.
func TestReadFrameRejectsMaskedServerFrame(t *testing.T) {
	payload := []byte("hello")
	maskKey := [4]byte{0x01, 0x02, 0x03, 0x04}
	masked := make([]byte, len(payload))
	for i, b := range payload {
		masked[i] = b ^ maskKey[i%4]
	}

	var raw []byte
	raw = append(raw, 0x80|0x1)               // fin, text
	raw = append(raw, 0x80|byte(len(masked))) // masked bit set, length
	raw = append(raw, maskKey[:]...)
	raw = append(raw, masked...)

	c := newConn(raw)

	_, _, _, err := c.readFrame()
	if err == nil {
		t.Fatal("expected an error for a masked server frame, got nil")
	}
}

// TestReadFrameRejectsRSVBits feeds a frame with a reserved bit set,
// which the server must not send unless an extension negotiated it (none
// is here).
func TestReadFrameRejectsRSVBits(t *testing.T) {
	var raw []byte
	raw = append(raw, 0x80|0x40|0x1) // fin, RSV1, text
	raw = append(raw, 0x00)          // unmasked, zero-length payload

	c := newConn(raw)

	_, _, _, err := c.readFrame()
	if err == nil {
		t.Fatal("expected an error for a frame with RSV bits set, got nil")
	}
}

// TestReadFrameAcceptsWellFormedFrame is the control: an ordinary
// unmasked text frame within the size cap must still work.
func TestReadFrameAcceptsWellFormedFrame(t *testing.T) {
	payload := []byte("hello")
	var raw []byte
	raw = append(raw, 0x80|0x1)
	raw = append(raw, byte(len(payload)))
	raw = append(raw, payload...)

	c := newConn(raw)

	fin, opcode, got, err := c.readFrame()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !fin || opcode != 0x1 {
		t.Fatalf("fin=%v opcode=%x, want fin=true opcode=0x1", fin, opcode)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("payload = %q, want %q", got, payload)
	}
}
