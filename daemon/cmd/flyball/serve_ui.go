package main

import (
	"context"
	"fmt"
	"io/fs"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"

	"flyballd/internal/webui"
)

// serveUI serves the embedded dashboard UI at "/" and reverse-proxies
// /api, /ws and /mcp to the runner on 127.0.0.1:port -- so `flyball run
// RIG-FILE --serve-ui :80` is reachable on its own, no nginx or other
// reverse proxy needed in front of it. Runs until ctx is cancelled
// (runDirect cancels it once the runner itself exits).
func serveUI(ctx context.Context, addr string, port string) error {
	dist, err := fs.Sub(webui.Dist, "dist")
	if err != nil {
		return fmt.Errorf("embedded UI: %w", err)
	}

	target, err := url.Parse("http://127.0.0.1:" + port)
	if err != nil {
		return fmt.Errorf("invalid runner port %q: %w", port, err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)

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
