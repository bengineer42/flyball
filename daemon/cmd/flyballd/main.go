// Command flyballd is the daemon from brain/plans/rig-deployment/plan.md:
// supervises runners (flyball-runner processes, one per rig), keeps the
// live registry, and serves the external interface from interface.md.
// Not the install script (plan.md's install-script section) -- this is
// just the binary it installs.
package main

import (
	"flag"
	"log"
	"net/http"
	"path/filepath"
	"time"

	"flyballd/internal/api"
	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/registry"
)

func main() {
	configPath := flag.String("config", "flyballd.yaml", "layer-1 daemon config")
	flag.Parse()

	daemonCfg, err := config.LoadDaemonConfig(*configPath)
	if err != nil {
		log.Fatalf("loading daemon config: %v", err)
	}

	logDir := filepath.Join(daemonCfg.DataDir, "logs")
	be, err := backend.NewProcessBackend(logDir, daemonCfg.LogMaxSize)
	if err != nil {
		log.Fatalf("starting process backend: %v", err)
	}

	reg := registry.New(be)

	manifests, err := config.LoadManifests(daemonCfg.ManifestsDir)
	if err != nil {
		log.Fatalf("loading manifests: %v", err)
	}
	for _, m := range manifests {
		if !m.IsEnabled() {
			continue
		}
		log.Printf("starting runner %q (%s)", m.Name, m.ServerConfig)
		if err := reg.Start(m); err != nil {
			log.Printf("failed to start runner %q: %v", m.Name, err)
		}
	}

	srv := api.New(reg, daemonCfg)
	if daemonCfg.Auth.Token == "" {
		log.Printf("no auth.token in %s: runners from %s are up, but the API will refuse to list, start, stop, restart or read them", *configPath, daemonCfg.ManifestsDir)
	}
	for _, w := range api.StartupWarnings(daemonCfg) {
		log.Printf("WARNING: %s", w)
	}
	log.Printf("flyballd listening on %s", daemonCfg.Listen)
	if err := newHTTPServer(daemonCfg.Listen, srv).ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}

// Variables so a test can shorten them.
var (
	readHeaderTimeout = 10 * time.Second
	idleTimeout       = 120 * time.Second
)

// newHTTPServer bounds what a client can hold open without doing
// anything: request headers must arrive within readHeaderTimeout, in at
// most 64 KiB, and an idle keep-alive connection is closed after
// idleTimeout. There is deliberately no ReadTimeout or WriteTimeout --
// those bound the whole request and response, and would cut off log
// streaming and websockets proxied to a runner.
func newHTTPServer(addr string, h http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           h,
		ReadHeaderTimeout: readHeaderTimeout,
		IdleTimeout:       idleTimeout,
		MaxHeaderBytes:    64 << 10,
	}
}
