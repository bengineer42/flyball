package principal

import (
	"encoding/hex"
	"testing"
	"time"
)

// FuzzVerify feeds arbitrary tokens to Verify and requires it never panic,
// regardless of key or clock. It seeds from the golden vectors so the
// corpus starts from real tokens and their near neighbours.
func FuzzVerify(f *testing.F) {
	vf, err := readVectorFile()
	if err != nil {
		f.Fatalf("loading vectors: %v", err)
	}
	for _, v := range vf.Vectors {
		f.Add(v.Token, v.Key, v.Now)
	}
	f.Add("", "", int64(0))
	f.Add("v1..", "00", int64(0))
	f.Add("v1.YQ.YQ", "0011223344556677889900112233445566778899001122334455667788990a", int64(1790000000))

	f.Fuzz(func(t *testing.T, token, keyHex string, now int64) {
		var key Key
		if b, err := hex.DecodeString(keyHex); err == nil && len(b) == len(key) {
			copy(key[:], b)
		}
		defer func() {
			if r := recover(); r != nil {
				t.Fatalf("Verify panicked on token %q: %v", token, r)
			}
		}()
		_, _ = Verify(token, key, "blender", time.Unix(now, 0))
	})
}
