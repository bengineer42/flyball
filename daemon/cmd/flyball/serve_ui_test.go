package main

import (
	"bytes"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
)

// newRefusingProxy builds a reverse proxy pointed at a port nothing is
// listening on (dialing it always fails with ECONNREFUSED, the same as a
// runner that hasn't started yet), wired up with our quiet-startup
// ErrorHandler.
func newRefusingProxy(t *testing.T) (*httputil.ReverseProxy, string, *atomic.Bool) {
	t.Helper()

	// Grab a free port, then stop listening on it so a dial refuses.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("net.Listen: %v", err)
	}
	addr := ln.Addr().String()
	ln.Close()

	target, err := url.Parse("http://" + addr)
	if err != nil {
		t.Fatalf("url.Parse: %v", err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	var ready atomic.Bool
	proxy.ModifyResponse = func(*http.Response) error {
		ready.Store(true)
		return nil
	}
	proxy.ErrorHandler = quietStartupErrors(&ready)
	return proxy, addr, &ready
}

func doProxied(proxy *httputil.ReverseProxy) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/api/status", nil)
	proxy.ServeHTTP(rec, req)
	return rec
}

// TestServeUIQuietBeforeUpstreamAnswers checks that a connection-refused
// error, before the runner has ever answered a request, gets a plain
// error response and produces no log line -- the noisy-startup case this
// ErrorHandler exists to fix.
func TestServeUIQuietBeforeUpstreamAnswers(t *testing.T) {
	proxy, _, ready := newRefusingProxy(t)

	var logBuf bytes.Buffer
	restore := log.Writer()
	log.SetOutput(&logBuf)
	defer log.SetOutput(restore)

	rec := doProxied(proxy)

	if rec.Code != http.StatusBadGateway && rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("code = %d, want 502 or 503", rec.Code)
	}
	if logBuf.Len() != 0 {
		t.Fatalf("logged output before the runner ever answered: %q", logBuf.String())
	}
	if ready.Load() {
		t.Fatal("ready should still be false: nothing has answered yet")
	}
}

// TestServeUILogsAfterUpstreamHasAnswered checks that once the runner has
// answered a request at least once, a later connection-refused error (the
// runner having gone away again) is logged as before.
func TestServeUILogsAfterUpstreamHasAnswered(t *testing.T) {
	proxy, addr, ready := newRefusingProxy(t)

	// Bring the "runner" up on the same address and let the proxy reach it
	// once.
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatalf("net.Listen: %v", err)
	}
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})}
	go srv.Serve(ln)
	defer srv.Close()

	if rec := doProxied(proxy); rec.Code != http.StatusOK {
		t.Fatalf("first request to the up runner: code = %d, want 200", rec.Code)
	}
	if !ready.Load() {
		t.Fatal("ready should be true once the runner has answered")
	}

	// Take the runner down again and confirm the next connection-refused
	// error is logged, not swallowed.
	srv.Close()
	ln.Close()

	var logBuf bytes.Buffer
	restore := log.Writer()
	log.SetOutput(&logBuf)
	defer log.SetOutput(restore)

	rec := doProxied(proxy)
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("code = %d, want 502", rec.Code)
	}
	if !strings.Contains(logBuf.String(), "proxy error") {
		t.Fatalf("expected a logged proxy error once the runner is known to be up, got %q", logBuf.String())
	}
}
