// `flyball token create|list|revoke`: offline operations on a front's
// named-tokens file (daemon/internal/front/store.Tokens) -- no runner or
// daemon request, no HTTP at all. This is the headless bootstrap
// (auth.md § "How tokens are made in Phase 1", auth-review.md rv-codebase
// Q1/C8): a Pi with no browser mints its own service token before the
// front is even listening, by writing straight into the file the front
// (or `flyball login`/the token routes) also reads. The file is under
// its own lock (store.OpenTokens), so this is safe to run against a live
// front -- it re-reads on the next Lookup, no restart needed.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front/store"
	"flyballd/internal/grants"

	"gopkg.in/yaml.v3"
)

func runTokenCommand(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("usage: flyball token create|list|revoke ...")
	}
	switch args[0] {
	case "create":
		return runTokenCreate(args[1:])
	case "list":
		return runTokenList(args[1:])
	case "revoke":
		return runTokenRevoke(args[1:])
	default:
		return fmt.Errorf("unknown token command %q; want create, list or revoke", args[0])
	}
}

func runTokenCreate(args []string) error {
	name, args, _ := popValue(args, "--name")
	config, args, _ := popValue(args, "--config")
	kind, args, _ := popValue(args, "--kind")
	expires, args, hasExpires := popValue(args, "--expires")
	scopes, args := popAllValues(args, "--scope")
	if name == "" || config == "" || len(args) != 0 {
		return fmt.Errorf("usage: flyball token create --name NAME --config PATH [--scope SCOPE ...] [--kind human|service|agent] [--expires DURATION]")
	}
	if len(scopes) == 0 {
		scopes = []string{grants.Read} // auth.md: "Default scope for automation, MCP included: read"
	}
	normalized, err := grants.NormalizeScopes(scopes)
	if err != nil {
		return err
	}
	var life time.Duration
	if hasExpires {
		life, err = parseExpires(expires)
		if err != nil {
			return err
		}
	}

	path, err := tokensPathFor(config)
	if err != nil {
		return err
	}
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		return err
	}
	defer tokens.Close()

	secret, tok, err := tokens.Create(store.NewToken{Name: name, Scopes: normalized, Kind: kind, ExpiresIn: life})
	if err != nil {
		return err
	}
	// The secret, and only the secret, on stdout -- everything else
	// (usable for scripting a systemd EnvironmentFile, say) on stderr, so
	// `flyball token create ... > token.txt` captures just the token
	// (never argv: the created NAME/SCOPE/KIND/EXPIRES aren't secrets,
	// but the minted secret never is one either, on the command line).
	fmt.Println(secret)
	fmt.Fprintf(os.Stderr, "token %s: name=%q scopes=%s kind=%s expires=%s\n",
		tok.ID, tok.Name, strings.Join(tok.Scopes, ","), tok.Kind, tok.Expires.Format(time.RFC3339))
	return nil
}

func runTokenList(args []string) error {
	config, args, _ := popValue(args, "--config")
	if config == "" || len(args) != 0 {
		return fmt.Errorf("usage: flyball token list --config PATH")
	}
	path, err := tokensPathFor(config)
	if err != nil {
		return err
	}
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		return err
	}
	defer tokens.Close()
	list, err := tokens.List()
	if err != nil {
		return err
	}
	tw := tabwriter.NewWriter(os.Stdout, 0, 4, 2, ' ', 0)
	fmt.Fprintln(tw, "ID\tNAME\tSCOPES\tKIND\tCREATED\tEXPIRES\tLAST USED")
	for _, t := range list {
		used := "never"
		if t.LastUsed != nil {
			used = t.LastUsed.Format(time.RFC3339)
		}
		fmt.Fprintf(tw, "%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
			t.ID, t.Name, strings.Join(t.Scopes, ","), t.Kind,
			t.Created.Format(time.RFC3339), t.Expires.Format(time.RFC3339), used)
	}
	return tw.Flush()
}

func runTokenRevoke(args []string) error {
	config, args, _ := popValue(args, "--config")
	if config == "" || len(args) != 1 {
		return fmt.Errorf("usage: flyball token revoke ID --config PATH")
	}
	path, err := tokensPathFor(config)
	if err != nil {
		return err
	}
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		return err
	}
	defer tokens.Close()
	if err := tokens.Revoke(args[0]); err != nil {
		return err
	}
	fmt.Println("revoked", args[0])
	return nil
}

// popAllValues collects every occurrence of --name VALUE (order
// preserved), for flags that may repeat (--scope), returning the values
// and the remaining args with all of them removed.
func popAllValues(args []string, name string) ([]string, []string) {
	var values []string
	out := make([]string, 0, len(args))
	for i := 0; i < len(args); i++ {
		if args[i] == name && i+1 < len(args) {
			values = append(values, args[i+1])
			i++
			continue
		}
		out = append(out, args[i])
	}
	return values, out
}

// parseExpires is time.ParseDuration plus a whole-days form ("30d"),
// matching auth.md's examples (`--expires 30d`) -- Go's own duration
// syntax has no unit past hours.
func parseExpires(s string) (time.Duration, error) {
	if days, ok := strings.CutSuffix(s, "d"); ok {
		n, err := strconv.Atoi(days)
		if err != nil {
			return 0, fmt.Errorf("--expires %q: %w", s, err)
		}
		if n < 0 {
			return 0, fmt.Errorf("--expires %q: a token's lifetime must be positive", s)
		}
		return time.Duration(n) * 24 * time.Hour, nil
	}
	d, err := time.ParseDuration(s)
	if err != nil {
		return 0, fmt.Errorf("--expires %q: %w (also accepts a whole number of days, e.g. 30d)", s, err)
	}
	return d, nil
}

// tokensPathFor is where --config PATH's tokens.json lives (§WP0-4):
//
//   - PATH looks like flyballd.yaml (has a manifests_dir key, the layer-1
//     key no rig file or `flyball run` config has) -> its data_dir
//     ("data" by default, matching config.DefaultDaemonConfig, resolved
//     the same way flyballd itself resolves it: relative to the process's
//     cwd, not to PATH) + "front/tokens.json";
//   - otherwise PATH is a rig file -> the `flyball run` front-id path,
//     $XDG_STATE_HOME/flyball/front-<id>/tokens.json, id =
//     frontdir.FrontID(the absolute rig path), the same id `flyball run`
//     computes from its own first rig file.
//
// This mirrors flyballd's own `--config` flag (cmd/flyballd/main.go) by
// name and shape deliberately: `flyball token create --config
// flyballd.yaml` bootstraps a daemon-fronted token exactly the way
// `flyballd --config flyballd.yaml` starts that same daemon.
func tokensPathFor(config string) (string, error) {
	daemon, err := looksLikeDaemonConfig(config)
	if err != nil {
		return "", err
	}
	if daemon {
		data, err := os.ReadFile(config)
		if err != nil {
			return "", fmt.Errorf("reading %s: %w", config, err)
		}
		var cfg struct {
			DataDir string `yaml:"data_dir"`
		}
		cfg.DataDir = "data"
		if err := yaml.Unmarshal(data, &cfg); err != nil {
			return "", fmt.Errorf("parsing %s: %w", config, err)
		}
		return filepath.Join(cfg.DataDir, "front", "tokens.json"), nil
	}
	id, err := frontdir.FrontID(config)
	if err != nil {
		return "", fmt.Errorf("%s: %w", config, err)
	}
	stateHome := os.Getenv("XDG_STATE_HOME")
	if stateHome == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("finding a state directory: %w", err)
		}
		stateHome = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(stateHome, "flyball", "front-"+id, "tokens.json"), nil
}

// looksLikeDaemonConfig: config parses as YAML with a top-level
// manifests_dir key, the layer-1-only marker (config.DaemonConfig).
func looksLikeDaemonConfig(config string) (bool, error) {
	data, err := os.ReadFile(config)
	if os.IsNotExist(err) {
		// Doesn't exist yet: nothing to sniff. Treated as a rig file --
		// `flyball run` would create that front's tokens file at first
		// use the same way, and a daemon config missing entirely is a
		// setup error `flyball daemon` / `flyballd` itself will report.
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("reading %s: %w", config, err)
	}
	var top map[string]any
	if err := yaml.Unmarshal(data, &top); err != nil {
		return false, fmt.Errorf("parsing %s: %w", config, err)
	}
	_, has := top["manifests_dir"]
	return has, nil
}
