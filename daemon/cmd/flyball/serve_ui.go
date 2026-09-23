package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"flyballd/internal/exposure"
	"flyballd/internal/webui"
)

// serveUI serves the embedded dashboard UI at "/" and reverse-proxies
// /api, /ws and /mcp to the runner on 127.0.0.1:port -- so `flyball run
// RIG-FILE --serve-ui :80` is reachable on its own, no nginx or other
// reverse proxy needed in front of it. Runs until ctx is cancelled
// (runDirect cancels it once the runner itself exits). A non-nil plan is
// the front's own exposure beyond what the runner knows (it listens on
// loopback): it replaces `exposure` in the runner's GET /api/auth, so the
// dashboard warns about the front, not the runner behind it.
func serveUI(ctx context.Context, addr string, port string, plan *exposure.Plan) error {
	dist, err := fs.Sub(webui.Dist, "dist")
	if err != nil {
		return fmt.Errorf("embedded UI: %w", err)
	}

	target, err := url.Parse("http://127.0.0.1:" + port)
	if err != nil {
		return fmt.Errorf("invalid runner port %q: %w", port, err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)

	// The runner takes a couple of seconds to start listening, during
	// which every proxied request dials a refused connection -- noisy on
	// every `--serve-ui` start. Stay quiet about that specific error until
	// the runner has answered a request at least once, then log errors
	// the way httputil.ReverseProxy does by default.
	var upstreamReady atomic.Bool
	proxy.ModifyResponse = func(resp *http.Response) error {
		upstreamReady.Store(true)
		if plan != nil {
			return reportExposure(resp, *plan)
		}
		return nil
	}
	proxy.ErrorHandler = quietStartupErrors(&upstreamReady)

	mux := http.NewServeMux()
	mux.Handle("/api/", proxy)
	mux.Handle("/ws/", proxy)
	mux.Handle("/mcp/", proxy)
	mux.Handle("/", http.FileServer(http.FS(dist)))

	srv := &http.Server{Addr: addr, Handler: mux}
	errc := make(chan error, 1)
	go func() { errc <- srv.ListenAndServe() }()

	select {
	case err := <-errc:
		if err != nil && err != http.ErrServerClosed {
			return fmt.Errorf("UI server: %w", err)
		}
		return nil
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return srv.Shutdown(shutdownCtx)
	}
}

// reportExposure rewrites `exposure` in a GET /api/auth answer to plan's.
func reportExposure(resp *http.Response, plan exposure.Plan) error {
	if resp.StatusCode != http.StatusOK || !strings.HasSuffix(resp.Request.URL.Path, "/api/auth") ||
		resp.Header.Get("Content-Encoding") != "" {
		return nil
	}
	raw, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		return err
	}
	var body map[string]any
	if json.Unmarshal(raw, &body) == nil {
		body["exposure"] = plan.Exposure()
		if rewritten, err := json.Marshal(body); err == nil {
			raw = rewritten
		}
	}
	resp.Body = io.NopCloser(bytes.NewReader(raw))
	resp.ContentLength = int64(len(raw))
	resp.Header.Set("Content-Length", strconv.Itoa(len(raw)))
	return nil
}

// quietStartupErrors returns a ReverseProxy ErrorHandler that drops
// connection-refused errors silently while ready is still false (the
// runner hasn't answered a proxied request yet), answering with 503
// instead of logging. Once ready is true -- or for any other kind of
// error at any time -- it logs and answers 502, matching
// httputil.ReverseProxy's own default ErrorHandler.
func quietStartupErrors(ready *atomic.Bool) func(http.ResponseWriter, *http.Request, error) {
	return func(w http.ResponseWriter, r *http.Request, err error) {
		if !ready.Load() && isConnRefused(err) {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		log.Printf("http: proxy error: %v", err)
		w.WriteHeader(http.StatusBadGateway)
	}
}

// isConnRefused reports whether err is (or wraps) ECONNREFUSED, the error
// a dial gets while the runner hasn't started listening yet.
func isConnRefused(err error) bool {
	return errors.Is(err, syscall.ECONNREFUSED)
}
