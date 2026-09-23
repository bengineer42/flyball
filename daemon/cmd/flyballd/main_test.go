package main

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"
)

// serve runs newHTTPServer on a loopback port, with its timeouts shortened
// for the test.
func serve(t *testing.T, h http.Handler) string {
	t.Helper()
	oldHeader, oldIdle := readHeaderTimeout, idleTimeout
	readHeaderTimeout, idleTimeout = 200*time.Millisecond, 300*time.Millisecond
	t.Cleanup(func() { readHeaderTimeout, idleTimeout = oldHeader, oldIdle })

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv := newHTTPServer(ln.Addr().String(), h)
	go srv.Serve(ln)
	t.Cleanup(func() { srv.Close() })
	return ln.Addr().String()
}

// closedWithin reports whether the server closes conn within d.
func closedWithin(conn net.Conn, d time.Duration) bool {
	conn.SetReadDeadline(time.Now().Add(d))
	_, err := io.Copy(io.Discard, conn)
	return err == nil // EOF: closed by the server; a timeout is an error
}

func ok(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "ok") }

// A client that never finishes its request headers (slowloris) is cut off.
func TestSlowHeadersAreCutOff(t *testing.T) {
	addr := serve(t, http.HandlerFunc(ok))
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	io.WriteString(conn, "GET / HTTP/1.1\r\nHost: x\r\n")
	if !closedWithin(conn, 2*time.Second) {
		t.Error("a connection with unfinished headers was still open after 2 s")
	}
}

// An idle keep-alive connection is closed.
func TestIdleConnectionsAreClosed(t *testing.T) {
	addr := serve(t, http.HandlerFunc(ok))
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	io.WriteString(conn, "GET / HTTP/1.1\r\nHost: x\r\n\r\n")
	resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if !closedWithin(conn, 2*time.Second) {
		t.Error("an idle keep-alive connection was still open after 2 s")
	}
}

func TestOversizedHeadersAreRefused(t *testing.T) {
	addr := serve(t, http.HandlerFunc(ok))
	req, _ := http.NewRequest("GET", "http://"+addr+"/", nil)
	req.Header.Set("X-Big", strings.Repeat("a", 200<<10))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusRequestHeaderFieldsTooLarge {
		t.Errorf("200 KiB of headers: %d, want 431", resp.StatusCode)
	}
}

// A long response -- log streaming, a proxied websocket -- is not cut off
// by any of the timeouts: there is no whole-request or write timeout.
func TestLongResponsesSurvive(t *testing.T) {
	addr := serve(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for i := range 8 {
			fmt.Fprintf(w, "%d\n", i)
			w.(http.Flusher).Flush()
			time.Sleep(100 * time.Millisecond)
		}
	}))
	resp, err := http.Get("http://" + addr + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil || strings.Count(string(body), "\n") != 8 {
		t.Errorf("an 0.8 s stream: %q, %v", body, err)
	}
}

// A hijacked connection (a websocket upgrade proxied to a runner) that is
// quiet for longer than every timeout stays open.
func TestQuietHijackedConnectionsSurvive(t *testing.T) {
	addr := serve(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, rw, err := w.(http.Hijacker).Hijack()
		if err != nil {
			return
		}
		defer conn.Close()
		rw.WriteString("HTTP/1.1 101 Switching Protocols\r\nUpgrade: test\r\nConnection: Upgrade\r\n\r\n")
		rw.Flush()
		line, _ := rw.ReadString('\n') // the client speaks after 0.8 s
		rw.WriteString("echo " + line)
		rw.Flush()
	}))
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	io.WriteString(conn, "GET / HTTP/1.1\r\nHost: x\r\nUpgrade: test\r\nConnection: Upgrade\r\n\r\n")
	br := bufio.NewReader(conn)
	resp, err := http.ReadResponse(br, nil)
	if err != nil || resp.StatusCode != http.StatusSwitchingProtocols {
		t.Fatalf("upgrade: %v %v", resp, err)
	}
	time.Sleep(800 * time.Millisecond)
	io.WriteString(conn, "hello\n")
	conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	if got, err := br.ReadString('\n'); got != "echo hello\n" {
		t.Errorf("after 0.8 s quiet: %q, %v", got, err)
	}
}
