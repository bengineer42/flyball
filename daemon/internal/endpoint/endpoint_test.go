package endpoint

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// onWindows sets GOOS to "windows" for one test: tcp endpoints are
// Windows-only (D-044), so their syntax is exercised as that platform.
func onWindows(t *testing.T) {
	t.Helper()
	was := GOOS
	GOOS = "windows"
	t.Cleanup(func() { GOOS = was })
}

// Off Windows a tcp endpoint is refused, however well formed, naming
// D-044 and the unix socket; on Windows the same string parses (D-044).
func TestTCPOnlyOnWindows(t *testing.T) {
	was := GOOS
	t.Cleanup(func() { GOOS = was })
	for _, goos := range []string{"linux", "darwin", "freebsd"} {
		GOOS = goos
		if TCPAllowed() {
			t.Errorf("%s: TCPAllowed", goos)
		}
		for _, s := range []string{"tcp:127.0.0.1:8102", "tcp:[::1]:8102", "tcp:localhost:8102"} {
			e, err := Parse(s)
			if !errors.Is(err, ErrTCPRefused) || !strings.Contains(err.Error(), "D-044") || !strings.Contains(err.Error(), "unix socket") {
				t.Errorf("%s: Parse(%q) = %+v, %v; want ErrTCPRefused naming D-044 and the unix socket", goos, s, e, err)
			}
		}
		if err := (Endpoint{Network: "tcp", Address: "127.0.0.1:8102"}).Validate(); !errors.Is(err, ErrTCPRefused) {
			t.Errorf("%s: Validate of a hand-built tcp endpoint: %v", goos, err)
		}
		if _, err := Parse("unix:/run/flyball/oven/sock"); err != nil {
			t.Errorf("%s: unix: %v", goos, err)
		}
	}
	GOOS = "windows"
	if !TCPAllowed() {
		t.Error("windows: !TCPAllowed")
	}
	if _, err := Parse("tcp:127.0.0.1:8102"); err != nil {
		t.Errorf("windows: %v", err)
	}
	// A malformed tcp endpoint is refused for what is wrong with it, on
	// Windows as elsewhere.
	if _, err := Parse("tcp:0.0.0.0:8102"); err == nil || errors.Is(err, ErrTCPRefused) {
		t.Errorf("windows, not loopback: %v", err)
	}
}

func TestParseEndpoint(t *testing.T) {
	onWindows(t)
	for _, s := range []string{
		"unix:/run/flyball/oven/sock",
		"unix:/tmp/flyball-123/sock",
		"tcp:127.0.0.1:8102",
		"tcp:127.0.0.2:1",
		"tcp:[::1]:65535",
		"tcp:localhost:8102",
	} {
		e, err := Parse(s)
		if err != nil {
			t.Errorf("Parse(%q): %v", s, err)
			continue
		}
		if got := e.String(); got != s {
			t.Errorf("Parse(%q).String() = %q", s, got)
		}
		again, err := Parse(e.String())
		if err != nil || again != e {
			t.Errorf("round trip of %q: %+v, %v", s, again, err)
		}
	}
	if e, _ := Parse("unix:/a/sock"); e.Network != "unix" || e.Address != "/a/sock" {
		t.Errorf("unix fields: %+v", e)
	}
	if e, _ := Parse("tcp:127.0.0.1:8102"); e.Network != "tcp" || e.Address != "127.0.0.1:8102" {
		t.Errorf("tcp fields: %+v", e)
	}

	long := "/" + strings.Repeat("x", 100) // 101 bytes
	for s, why := range map[string]string{
		"":                       "empty",
		"unix:":                  "empty path",
		"unix:sock":              "relative path",
		"unix:./run/sock":        "relative path",
		"unix:/a/../b/sock":      "unclean path",
		"unix:" + long:           "path over 100 bytes",
		"tcp:0.0.0.0:8102":       "not loopback",
		"tcp:192.168.1.3:8102":   "not loopback",
		"tcp:[::]:8102":          "not loopback",
		"tcp:example.com:8102":   "a name other than localhost",
		"tcp:127.0.0.1":          "no port",
		"tcp:127.0.0.1:0":        "port 0",
		"tcp:127.0.0.1:65536":    "port out of range",
		"tcp:127.0.0.1:http":     "named port",
		"udp:127.0.0.1:8102":     "unknown network",
		"/run/flyball/oven/sock": "no network",
		"127.0.0.1:8102":         "no network",
		"unix:/a/sock\x00":       "NUL",
		"tcp: 127.0.0.1:8102":    "space",
	} {
		if e, err := Parse(s); err == nil {
			t.Errorf("Parse(%q) accepted (%s): %+v", s, why, e)
		}
	}
	// 100 bytes exactly is the limit, not over it.
	edge := "/" + strings.Repeat("x", 99)
	if _, err := Parse("unix:" + edge); err != nil {
		t.Errorf("a 100-byte path: %v", err)
	}
}

func TestURL(t *testing.T) {
	onWindows(t)
	for _, s := range []string{"unix:/a/sock", "tcp:127.0.0.1:8102"} {
		e, _ := Parse(s)
		if got := e.URL("/oven"); got != "http://localhost/oven" {
			t.Errorf("%s URL(/oven) = %q", s, got)
		}
		if got := e.URL(""); got != "http://localhost" {
			t.Errorf("%s URL(\"\") = %q", s, got)
		}
	}
}

// shortTemp is a temp dir short enough for a socket path (t.TempDir can
// exceed sun_path under a long TMPDIR).
func shortTemp(t *testing.T) string {
	t.Helper()
	dir, err := os.MkdirTemp("", "fb-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	return dir
}

// serveUnix serves h on a real unix socket at dir/sock.
func serveUnix(t *testing.T, dir string, h http.Handler) Endpoint {
	t.Helper()
	path := filepath.Join(dir, "sock")
	l, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{Handler: h}
	go srv.Serve(l)
	t.Cleanup(func() { srv.Close() })
	e, err := Parse("unix:" + path)
	if err != nil {
		t.Fatal(err)
	}
	return e
}

// The transport dials the endpoint whatever host the URL names.
func TestTransportDialsTheEndpoint(t *testing.T) {
	e := serveUnix(t, shortTemp(t), http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "host="+r.Host+" path="+r.URL.Path)
	}))
	c := &http.Client{Transport: e.Transport(), Timeout: 2 * time.Second}
	for _, u := range []string{e.URL("/oven") + "/api/x", "http://evil.example/oven/api/x"} {
		resp, err := c.Get(u)
		if err != nil {
			t.Fatalf("%s: %v", u, err)
		}
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if !strings.HasSuffix(string(b), "path=/oven/api/x") {
			t.Errorf("%s: %q", u, b)
		}
	}
	if tr := e.Transport(); tr.Proxy != nil {
		t.Error("the transport honours HTTP_PROXY; it must always dial the endpoint")
	}
}

// fakeSign is a stand-in for principal.Mint: the header carries the key's
// hex and the aud, so a fake runner can check it was signed with its key.
func fakeSign(r *http.Request, key [32]byte, aud string) error {
	r.Header.Set("X-Flyball-Principal", fakeToken(key, aud))
	return nil
}

func fakeToken(key [32]byte, aud string) string {
	const hex = "0123456789abcdef"
	var b strings.Builder
	for _, c := range key {
		b.WriteByte(hex[c>>4])
		b.WriteByte(hex[c&15])
	}
	return b.String() + "/" + aud
}

// fakeFront answers GET /oven/api/auth/front as a fronted runner does, or
// as told to misbehave.
func fakeFront(key [32]byte, aud string, unsigned, signed int, bodyAud string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/oven/api/auth/front" {
			http.NotFound(w, r)
			return
		}
		p := r.Header.Get("X-Flyball-Principal")
		if p == "" {
			w.WriteHeader(unsigned)
			return
		}
		if p != fakeToken(key, aud) {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.WriteHeader(signed)
		io.WriteString(w, `{"protocol":1,"aud":"`+bodyAud+`","pid":4242,"flyball_version":"0.9.0"}`)
	})
}

func TestProbeOverUnix(t *testing.T) {
	key := [32]byte{1, 2, 3}
	ctx := context.Background()

	// Nothing there yet: ENOENT. A socket file nobody listens on:
	// ECONNREFUSED. Both are starting, not failures.
	dir := shortTemp(t)
	missing, _ := Parse("unix:" + filepath.Join(dir, "sock"))
	if _, err := Handshake(ctx, missing, "/oven", "oven", key, fakeSign); !errors.Is(err, ErrNotListening) {
		t.Errorf("no socket (ENOENT): %v, want ErrNotListening", err)
	}
	l, err := net.Listen("unix", missing.Address)
	if err != nil {
		t.Fatal(err)
	}
	l.(*net.UnixListener).SetUnlinkOnClose(false)
	l.Close()
	if _, err := os.Stat(missing.Address); err != nil {
		t.Fatalf("stale socket file gone: %v", err)
	}
	if _, err := Handshake(ctx, missing, "/oven", "oven", key, fakeSign); !errors.Is(err, ErrNotListening) {
		t.Errorf("stale socket (ECONNREFUSED): %v, want ErrNotListening", err)
	}
	onWindows(t)
	tcp, _ := Parse("tcp:127.0.0.1:1")
	if _, err := Handshake(ctx, tcp, "/oven", "oven", key, fakeSign); !errors.Is(err, ErrNotListening) {
		t.Errorf("tcp refused: %v, want ErrNotListening", err)
	}

	// Running needs both halves: unsigned 401 and signed 200 for this aud.
	good := serveUnix(t, shortTemp(t), fakeFront(key, "oven", 401, 200, "oven"))
	info, err := Handshake(ctx, good, "/oven", "oven", key, fakeSign)
	if err != nil {
		t.Fatalf("a fronted runner: %v", err)
	}
	if info.Protocol != 1 || info.Aud != "oven" || info.Pid != 4242 || info.FlyballVersion != "0.9.0" {
		t.Errorf("info: %+v", info)
	}

	for name, h := range map[string]http.Handler{
		"unsigned 200 (does not enforce the principal)": fakeFront(key, "oven", 200, 200, "oven"),
		"unsigned 404 (an old runner)":                  fakeFront(key, "oven", 404, 200, "oven"),
		"signed 401 (another key)":                      fakeFront([32]byte{9}, "oven", 401, 200, "oven"),
		"signed 200 for another aud":                    fakeFront(key, "oven", 401, 200, "kiln"),
		"signed 500":                                    fakeFront(key, "oven", 401, 500, "oven"),
	} {
		e := serveUnix(t, shortTemp(t), h)
		_, err := Handshake(ctx, e, "/oven", "oven", key, fakeSign)
		if err == nil || errors.Is(err, ErrNotListening) {
			t.Errorf("%s: %v, want a refusal", name, err)
		}
	}

	// No signer: never ready.
	if _, err := Handshake(ctx, good, "/oven", "oven", key, nil); err == nil {
		t.Error("ready with no signer")
	}
}
