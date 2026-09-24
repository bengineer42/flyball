// Package principal mints and verifies flyball's v1 signed principal: the
// HMAC-SHA256 token a front attaches to every request it proxies to a
// runner, carrying the caller's identity and verbs on that rig.
//
// Format (see brain/design/auth.md, "The signed principal"):
//
//	token   = "v1." B64(payload) "." B64(HMAC-SHA256(key, ASCII("v1." B64(payload))))
//	B64     = base64url, RFC 4648 section 5, no padding
//	payload = UTF-8 JSON object, keys in a fixed order, no whitespace, no HTML escaping
//
// Golden vectors: daemon/internal/principal/testdata/principal-v1.json.
package principal

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"
)

// Header is the HTTP header a front sets and a runner reads.
const Header = "X-Flyball-Principal"

// Lifetime is the span a minted principal carries between iat and exp.
const Lifetime = 60 * time.Second

// leeway is the clock-skew tolerance Verify allows past exp and before iat.
const leeway = 5 * time.Second

// maxLifetime is the largest exp-iat span Verify accepts, regardless of who
// minted the token.
const maxLifetimeSeconds = 120

// maxTokenBytes is the largest token Verify will look at.
const maxTokenBytes = 4096

// Key is the 32-byte per-runner HMAC-SHA256 key.
type Key [32]byte

// Claims are a principal's payload. Field order matches the mint order in
// brain/design/auth.md: sub, nm?, sid, scp, kind, aud, cip, sch, via?, iat, exp.
type Claims struct {
	Sub  string   `json:"sub"`
	Nm   string   `json:"nm,omitempty"`
	Sid  string   `json:"sid"`
	Scp  []string `json:"scp"`
	Kind string   `json:"kind"` // "human" | "service" | "agent"
	Aud  string   `json:"aud"`
	Cip  string   `json:"cip"`
	Sch  string   `json:"sch"` // "http" | "https"
	Via  string   `json:"via,omitempty"`
	Iat  int64    `json:"iat"`
	Exp  int64    `json:"exp"`
}

// Error is what Verify and Mint return on refusal. Code is one of the nine
// wire codes, checked in this order: format, version, mac, json, claims,
// aud, lifetime, expired, future.
type Error struct {
	Code string
}

func (e *Error) Error() string { return e.Code }

func refuse(code string) *Error { return &Error{Code: code} }

var b64 = base64.RawURLEncoding

// b64Pattern matches base64url (no padding) alphabet only; it exists so a
// padded or otherwise foreign string is refused as "format" before an
// attempted decode.
var b64Pattern = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

// disallowedRunes are the characters mint refuses in any string claim, so
// Go and Python mint the same bytes for the same input.
func hasDisallowedRune(s string) bool {
	for _, r := range s {
		if (r >= 0x00 && r <= 0x1f) || r == ' ' || r == ' ' {
			return true
		}
	}
	return false
}

// Mint builds and signs a v1 principal for c. It sets nothing implicitly:
// the caller fills Iat and Exp. Scp is sorted ascending and de-duplicated.
// Mint refuses any string claim (including each Scp entry) containing
// U+0000-U+001F, U+2028 or U+2029.
func Mint(k Key, c Claims) (string, error) {
	strings_ := []string{c.Sub, c.Nm, c.Sid, c.Kind, c.Aud, c.Cip, c.Sch, c.Via}
	for _, s := range strings_ {
		if hasDisallowedRune(s) {
			return "", fmt.Errorf("principal: claim contains a disallowed control character")
		}
	}

	seen := make(map[string]bool, len(c.Scp))
	scp := make([]string, 0, len(c.Scp))
	for _, s := range c.Scp {
		if hasDisallowedRune(s) {
			return "", fmt.Errorf("principal: scp contains a disallowed control character")
		}
		if !seen[s] {
			seen[s] = true
			scp = append(scp, s)
		}
	}
	sort.Strings(scp)
	c.Scp = scp

	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(c); err != nil {
		return "", err
	}
	payload := bytes.TrimSuffix(buf.Bytes(), []byte("\n"))

	signing := "v1." + b64.EncodeToString(payload)
	mac := hmac.New(sha256.New, k[:])
	mac.Write([]byte(signing))
	return signing + "." + b64.EncodeToString(mac.Sum(nil)), nil
}

// payloadOf returns the payload bytes of a "v1.<payload>.<mac>" token,
// without verifying anything. It exists for tests that check mint's byte
// output against a vector's expected payload.
func payloadOf(token string) ([]byte, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, fmt.Errorf("principal: not a v1 token")
	}
	return b64.DecodeString(parts[1])
}

// ReadKeyFile reads a per-runner key: exactly 64 hex characters, with an
// optional single trailing newline, and nothing else.
func ReadKeyFile(path string) (Key, error) {
	var key Key
	raw, err := os.ReadFile(path)
	if err != nil {
		return key, err
	}
	s := string(raw)
	if strings.HasSuffix(s, "\n") {
		s = strings.TrimSuffix(s, "\n")
	}
	if len(s) != 64 {
		return key, fmt.Errorf("principal: key file must hold 64 hex characters, got %d", len(s))
	}
	decoded, err := hex.DecodeString(s)
	if err != nil {
		return key, fmt.Errorf("principal: key file is not valid hex: %w", err)
	}
	copy(key[:], decoded)
	return key, nil
}

// Verify checks token's format, MAC, and claims against key, aud and now,
// in the order the wire format requires, and returns the decoded Claims on
// success.
func Verify(token string, k Key, aud string, now time.Time) (Claims, error) {
	var zero Claims

	if len(token) == 0 || len(token) > maxTokenBytes {
		return zero, refuse("format")
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return zero, refuse("format")
	}
	for _, p := range parts {
		if p == "" {
			return zero, refuse("format")
		}
	}
	if !b64Pattern.MatchString(parts[1]) || !b64Pattern.MatchString(parts[2]) {
		return zero, refuse("format")
	}
	payload, err := b64.DecodeString(parts[1])
	if err != nil {
		return zero, refuse("format")
	}
	mac, err := b64.DecodeString(parts[2])
	if err != nil {
		return zero, refuse("format")
	}

	if parts[0] != "v1" {
		return zero, refuse("version")
	}

	h := hmac.New(sha256.New, k[:])
	h.Write([]byte(parts[0] + "." + parts[1]))
	want := h.Sum(nil)
	if len(mac) != len(want) || !hmac.Equal(mac, want) {
		return zero, refuse("mac")
	}

	dec := json.NewDecoder(bytes.NewReader(payload))
	dec.UseNumber()
	var raw map[string]any
	if err := dec.Decode(&raw); err != nil || raw == nil {
		return zero, refuse("json")
	}

	claims, rerr := claimsFromMap(raw)
	if rerr != nil {
		return zero, rerr
	}

	if claims.Aud != aud {
		return zero, refuse("aud")
	}

	lifetime := claims.Exp - claims.Iat
	if lifetime <= 0 || lifetime > maxLifetimeSeconds {
		return zero, refuse("lifetime")
	}

	nowS := now.Unix()
	if nowS > claims.Exp+int64(leeway.Seconds()) {
		return zero, refuse("expired")
	}
	if claims.Iat > nowS+int64(leeway.Seconds()) {
		return zero, refuse("future")
	}

	return claims, nil
}

// claimsFromMap validates and converts a decoded JSON object into Claims,
// per the "claims" check: required non-empty strings, optional strings
// that may be absent or empty, an enum for kind and sch, a string array
// for scp, and integer (not float, not exponent, not oversized) iat/exp.
func claimsFromMap(raw map[string]any) (Claims, *Error) {
	var zero Claims

	str := func(key string, optional, allowEmpty bool) (string, bool) {
		v, ok := raw[key]
		if !ok {
			return "", optional
		}
		s, ok := v.(string)
		if !ok {
			return "", false
		}
		if !allowEmpty && s == "" {
			return "", false
		}
		return s, true
	}

	sub, ok := str("sub", false, false)
	if !ok {
		return zero, refuse("claims")
	}
	sid, ok := str("sid", false, false)
	if !ok {
		return zero, refuse("claims")
	}
	aud, ok := str("aud", false, false)
	if !ok {
		return zero, refuse("claims")
	}
	cip, ok := str("cip", false, true)
	if !ok {
		return zero, refuse("claims")
	}
	nm, ok := str("nm", true, true)
	if !ok {
		return zero, refuse("claims")
	}
	via, ok := str("via", true, true)
	if !ok {
		return zero, refuse("claims")
	}

	kind, ok := raw["kind"].(string)
	if !ok || (kind != "human" && kind != "service" && kind != "agent") {
		return zero, refuse("claims")
	}
	sch, ok := raw["sch"].(string)
	if !ok || (sch != "http" && sch != "https") {
		return zero, refuse("claims")
	}

	scpAny, ok := raw["scp"].([]any)
	if !ok {
		return zero, refuse("claims")
	}
	scp := make([]string, 0, len(scpAny))
	for _, x := range scpAny {
		s, ok := x.(string)
		if !ok || s == "" {
			return zero, refuse("claims")
		}
		scp = append(scp, s)
	}

	ints := make(map[string]int64, 2)
	for _, key := range []string{"iat", "exp"} {
		n, ok := raw[key].(json.Number)
		if !ok || strings.ContainsAny(n.String(), ".eE-") {
			return zero, refuse("claims")
		}
		v, err := n.Int64()
		if err != nil || v >= 1<<53 {
			return zero, refuse("claims")
		}
		ints[key] = v
	}

	return Claims{
		Sub:  sub,
		Nm:   nm,
		Sid:  sid,
		Scp:  scp,
		Kind: kind,
		Aud:  aud,
		Cip:  cip,
		Sch:  sch,
		Via:  via,
		Iat:  ints["iat"],
		Exp:  ints["exp"],
	}, nil
}
