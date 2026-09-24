package principal

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// vectorFile mirrors daemon/internal/principal/testdata/principal-v1.json.
type vectorFile struct {
	AudOfVerifier string   `json:"aud_of_verifier"`
	Vectors       []vector `json:"vectors"`
}

type vector struct {
	Name    string  `json:"name"`
	Key     string  `json:"key"`
	Now     int64   `json:"now"`
	Token   string  `json:"token"`
	Expect  string  `json:"expect"`
	Mint    bool    `json:"mint"`
	Claims  *Claims `json:"claims"`
	Payload string  `json:"payload"`
}

// readVectorFile has no *testing.T dependency so FuzzVerify can seed from
// it too.
func readVectorFile() (vectorFile, error) {
	raw, err := os.ReadFile(filepath.Join("testdata", "principal-v1.json"))
	if err != nil {
		return vectorFile{}, err
	}
	var vf vectorFile
	if err := json.Unmarshal(raw, &vf); err != nil {
		return vectorFile{}, err
	}
	return vf, nil
}

func loadVectors(t *testing.T) vectorFile {
	t.Helper()
	vf, err := readVectorFile()
	if err != nil {
		t.Fatalf("loading vectors: %v", err)
	}
	return vf
}

func TestVectors(t *testing.T) {
	vf := loadVectors(t)
	if len(vf.Vectors) != 19 {
		t.Fatalf("expected 19 vectors, got %d", len(vf.Vectors))
	}
	for _, v := range vf.Vectors {
		v := v
		t.Run(v.Name, func(t *testing.T) {
			keyBytes, err := hex.DecodeString(v.Key)
			if err != nil || len(keyBytes) != 32 {
				t.Fatalf("bad key hex in vector: %v", err)
			}
			var key Key
			copy(key[:], keyBytes)

			got := "ok"
			claims, err := Verify(v.Token, key, vf.AudOfVerifier, time.Unix(v.Now, 0))
			if err != nil {
				pe, ok := err.(*Error)
				if !ok {
					t.Fatalf("Verify returned non-*Error: %v", err)
				}
				got = pe.Code
			}
			if got != v.Expect {
				t.Errorf("Verify: expect %q, got %q", v.Expect, got)
			}
			if v.Expect == "ok" && err == nil {
				_ = claims
			}

			if v.Mint {
				if v.Claims == nil {
					t.Fatalf("mint vector %q has no claims", v.Name)
				}
				tok, err := Mint(key, *v.Claims)
				if err != nil {
					t.Fatalf("Mint: %v", err)
				}
				if tok != v.Token {
					t.Errorf("Mint token mismatch:\n got  %s\n want %s", tok, v.Token)
				}
				payload, err := payloadOf(tok)
				if err != nil {
					t.Fatalf("payloadOf: %v", err)
				}
				if string(payload) != v.Payload {
					t.Errorf("Mint payload mismatch:\n got  %s\n want %s", payload, v.Payload)
				}
			}
		})
	}
}

func TestMintRefusesControlChars(t *testing.T) {
	var key Key
	base := Claims{
		Sub:  "local:admin",
		Sid:  "s-1",
		Scp:  []string{"read"},
		Kind: "human",
		Aud:  "blender",
		Cip:  "",
		Sch:  "https",
		Iat:  1000,
		Exp:  1060,
	}

	cases := []struct {
		name string
		mod  func(c Claims) Claims
	}{
		{"nul-in-nm", func(c Claims) Claims { c.Nm = "bad\u0000name"; return c }},
		{"line-separator-in-nm", func(c Claims) Claims { c.Nm = "bad name"; return c }},
		{"paragraph-separator-in-sub", func(c Claims) Claims { c.Sub = "local: admin"; return c }},
		{"control-in-scp", func(c Claims) Claims { c.Scp = []string{"re\u0001ad"}; return c }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := Mint(key, tc.mod(base)); err == nil {
				t.Fatalf("Mint accepted a control character, want error")
			}
		})
	}
}

func TestReadKeyFile(t *testing.T) {
	dir := t.TempDir()

	write := func(name, content string) string {
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, []byte(content), 0o600); err != nil {
			t.Fatalf("writing fixture: %v", err)
		}
		return p
	}

	hex64 := "0678e74dbefdccd84f9b110b1ae6282825bdc7e006028620c03fd6331ed27f9f"
	if len(hex64) != 64 {
		t.Fatalf("test fixture bug: hex64 is %d chars", len(hex64))
	}

	t.Run("64 hex ok", func(t *testing.T) {
		p := write("key-ok", hex64)
		k, err := ReadKeyFile(p)
		if err != nil {
			t.Fatalf("ReadKeyFile: %v", err)
		}
		want, _ := hex.DecodeString(hex64)
		if !equalKey(k, want) {
			t.Errorf("key mismatch")
		}
	})

	t.Run("64 hex plus newline ok", func(t *testing.T) {
		p := write("key-nl", hex64+"\n")
		if _, err := ReadKeyFile(p); err != nil {
			t.Fatalf("ReadKeyFile: %v", err)
		}
	})

	t.Run("63 hex is an error", func(t *testing.T) {
		p := write("key-short", hex64[:63])
		if _, err := ReadKeyFile(p); err == nil {
			t.Fatalf("expected an error for a 63-char key")
		}
	})

	t.Run("uppercase-only garbage is an error", func(t *testing.T) {
		p := write("key-garbage", "GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG")
		if _, err := ReadKeyFile(p); err == nil {
			t.Fatalf("expected an error for non-hex garbage")
		}
	})

	t.Run("extra bytes is an error", func(t *testing.T) {
		p := write("key-extra", hex64+"00")
		if _, err := ReadKeyFile(p); err == nil {
			t.Fatalf("expected an error for extra bytes past the key")
		}
	})
}

func equalKey(k Key, want []byte) bool {
	if len(want) != len(k) {
		return false
	}
	for i := range k {
		if k[i] != want[i] {
			return false
		}
	}
	return true
}
