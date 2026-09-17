// Command flyballd is the daemon from brain/plans/rig-deployment/plan.md:
// supervises runners (flyball-daemon processes, one per rig), keeps the
// live registry, and serves the external interface from interface.md.
// Not the install script (plan.md's install-script section) -- this is
// just the binary it installs.
package main

import (
	"flag"
	"log"
	"net/http"
	"path/filepath"

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
	be, err := backend.NewProcessBackend(logDir)
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
	log.Printf("flyballd listening on %s", daemonCfg.Listen)
	if err := http.ListenAndServe(daemonCfg.Listen, srv); err != nil {
		log.Fatal(err)
	}
}
