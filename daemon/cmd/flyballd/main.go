// Command flyballd is the daemon from brain/plans/rig-deployment/plan.md:
// supervises runners (flyball-runner processes, one per rig), keeps the
// live registry, and serves them behind its front (package front): each
// rig under its root path, reached only through the front with a signed
// principal, and the daemon's own management API, which needs a bearer
// token with the management scope. Not the install script (plan.md's
// install-script section) -- this is just the binary it installs.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"flyballd/internal/api"
	"flyballd/internal/backend"
	"flyballd/internal/config"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front"
	"flyballd/internal/frontwire"
	"flyballd/internal/registry"
)

func main() {
	configPath := flag.String("config", "flyballd.yaml", "layer-1 daemon config")
	insecureOpen := flag.Bool("insecure-open", false, "serve the local shape (no login) on a listen address beyond loopback:"+
		" anyone who reaches it may operate every rig (env FLYBALL_INSECURE_OPEN=1). Per run only; never a file key")
	flag.Parse()

	if err := daemonMain(*configPath, *insecureOpen || frontwire.InsecureOpenEnv()); err != nil {
		log.Fatal(err)
	}
}

// daemonMain is flyballd until SIGINT or SIGTERM. Neither stops a runner
// (D-037): flyballd exits and its runners carry on, to be adopted by the
// next flyballd. `flyball runners stop` ends them.
func daemonMain(configPath string, insecureOpen bool) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	// SIGHUP does not stop flyballd: the TLS reloader re-reads its files on
	// it (tlsfile), and nothing else reloads.
	hup := make(chan os.Signal, 1)
	signal.Notify(hup, syscall.SIGHUP)
	defer signal.Stop(hup)
	go func() {
		for range hup {
			log.Printf("SIGHUP: TLS certificates (if any) are re-read; nothing else reloads -- restart flyballd for other changes")
		}
	}()
	return run(ctx, configPath, insecureOpen, nil)
}

// run is flyballd: it starts every enabled manifest's runner behind the
// front and serves until ctx is done, then lets go of its runners, which
// carry on (D-037). ready, if
// not nil, is told where the front listens.
func run(ctx context.Context, configPath string, insecureOpen bool, ready func(net.Addr)) error {
	cfg, err := config.LoadDaemonConfig(configPath)
	if err != nil {
		return fmt.Errorf("loading daemon config: %w", err)
	}

	be, err := backend.NewProcessBackend(filepath.Join(cfg.DataDir, "logs"), cfg.LogMaxSize)
	if err != nil {
		return fmt.Errorf("starting process backend: %w", err)
	}
	// Runners' front-dirs: /run/flyball/<name> under systemd, else
	// $XDG_RUNTIME_DIR/flyball/<front-id>/<name>, the front-id named after
	// this flyballd.yaml.
	abs, err := filepath.Abs(configPath)
	if err != nil {
		return err
	}
	id, err := frontdir.FrontID(abs)
	if err != nil {
		return err
	}
	root := frontdir.Root(id)
	if root == "" {
		log.Printf("WARNING: no private runtime dir (RUNTIME_DIRECTORY or XDG_RUNTIME_DIR, mode 0700): runners get temp front-dirs," +
			" which a restarted flyballd cannot find. A runner this flyballd leaves running (D-037) is not adopted by the next one:" +
			" that one's runner exits 3 on the rig's lock and the rig is busy while the old runner runs on, unreachable through" +
			" the front -- end it with `kill <pid>` (its pid is in <store>.lock) or stop the rig with `flyball stop --pid <pid>`")
	}
	// A runner already alive in its front-dir (left by a previous
	// flyballd) is adopted, not restarted (D-037).
	be.SetFront(backend.FrontOptions{Root: root, Sign: front.ProbeSigner(nil)})
	reg := registry.New(be)

	logger := slog.Default()
	state := frontwire.DaemonDir(cfg.DataDir)
	audit := frontwire.OpenAudit(state, logger)
	plan, proxy := frontwire.Plan(cfg.Front, cfg.FrontError, insecureOpen, frontwire.ProxyOptions{Logger: logger, Audit: audit})
	if b := plan.Banner(); b != "" {
		for _, line := range strings.Split(b, "\n") {
			log.Printf("WARNING: %s", line)
		}
	}

	manifests, err := config.LoadManifests(cfg.ManifestsDir)
	if err != nil {
		plan.Close()
		audit.Close()
		return fmt.Errorf("loading manifests: %w", err)
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
	// D-037: flyballd's exit, however it comes, leaves every runner
	// running; the next flyballd adopts them. `flyball runners stop` ends
	// them.
	defer be.Detach()

	f := api.NewFront(reg, frontwire.Options(plan, proxy, audit, state, logger))
	defer frontwire.Closer(f, plan, audit)()
	return frontwire.Serve(ctx, plan, f, func(a net.Addr) {
		log.Printf("flyballd listening on %s (%s); management needs a bearer token with the manage scope (flyball token create)", a, plan.Shape)
		if ready != nil {
			ready(a)
		}
	})
}
