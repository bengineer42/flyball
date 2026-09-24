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
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"text/tabwriter"
	"time"

	daemonconfig "flyballd/internal/config"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/front"
	"flyballd/internal/front/store"
	"flyballd/internal/frontwire"
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
	configs, args := popAllValues(args, "--config")
	sets, args := popSets(args)
	daemon, args := popBool(args, "--daemon")
	kind, args, _ := popValue(args, "--kind")
	expires, args, hasExpires := popValue(args, "--expires")
	scopes, args := popAllValues(args, "--scope")
	if name == "" || len(configs) == 0 || len(args) != 0 {
		return fmt.Errorf("usage: flyball token create --name NAME --config PATH [--config PATH ...] [--set KEY=VALUE ...] [--daemon] [--scope SCOPE ...] [--kind human|service|agent] [--expires DURATION]")
	}
	config := configs[0] // the tokens file is the first rig file's, as `flyball run`'s front is
	if len(scopes) == 0 {
		scopes = []string{grants.Read} // auth.md: "Default scope for automation, MCP included: read"
	}
	normalized, err := grants.NormalizeScopes(scopes)
	if err != nil {
		return err
	}
	normalized = addReadScopes(normalized)
	var life time.Duration
	if hasExpires {
		life, err = parseExpires(expires)
		if err != nil {
			return err
		}
	}

	path, err := tokensPathFor(config, daemon)
	if err != nil {
		return err
	}
	if err := refuseAnotherUsersState(path); err != nil {
		return err
	}
	lifetimes, warnings, err := lifetimesFor(configs, sets, daemon)
	if err != nil {
		return err
	}
	for _, w := range warnings {
		fmt.Fprintln(os.Stderr, "token create: "+w)
	}
	audit, err := openTokenAudit(path)
	if err != nil {
		return fmt.Errorf("%w; a token change must be recorded, so no token was created", err)
	}
	defer audit.Close()
	tokens, err := store.OpenTokens(path, store.TokensOptions{Lifetimes: lifetimes})
	if err != nil {
		return err
	}
	defer tokens.Close()

	secret, tok, err := tokens.Create(store.NewToken{Name: name, Scopes: normalized, Kind: kind, ExpiresIn: life})
	if err != nil {
		var pathErr *fs.PathError
		if !errors.As(err, &pathErr) {
			audit.Event("token.create.refused", slog.String("by", subCLI), slog.String("name", name), slog.String("why", err.Error()))
		}
		return err
	}
	if err := audit.Event("token.create", slog.String("by", subCLI), slog.String("id", tok.ID),
		slog.String("name", tok.Name), slog.Any("scopes", tok.Scopes), slog.String("kind", tok.Kind)); err != nil {
		tokens.Revoke(tok.ID)
		return fmt.Errorf("%w; a token change must be recorded, so no token was created", err)
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
	daemon, args := popBool(args, "--daemon")
	if config == "" || len(args) != 0 {
		return fmt.Errorf("usage: flyball token list --config PATH [--daemon]")
	}
	path, err := tokensPathFor(config, daemon)
	if err != nil {
		return err
	}
	if err := refuseAnotherUsersState(path); err != nil {
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
	daemon, args := popBool(args, "--daemon")
	if config == "" || len(args) != 1 {
		return fmt.Errorf("usage: flyball token revoke ID --config PATH [--daemon]")
	}
	path, err := tokensPathFor(config, daemon)
	if err != nil {
		return err
	}
	if err := refuseAnotherUsersState(path); err != nil {
		return err
	}
	tokens, err := store.OpenTokens(path, store.TokensOptions{})
	if err != nil {
		return err
	}
	defer tokens.Close()
	// Revoke first, then record how it ended, as the front does: a revoke
	// is the safe direction, so one that cannot be recorded still happens.
	id := args[0]
	err = tokens.Revoke(id)
	outcome := "revoked"
	switch {
	case errors.Is(err, store.ErrTokenNotFound):
		outcome = "not found"
	case err != nil:
		outcome = "failed"
	}
	audit, auditErr := openTokenAudit(path)
	if auditErr == nil {
		auditErr = audit.Event("token.revoke", slog.String("by", subCLI), slog.String("id", id), slog.String("outcome", outcome))
		audit.Close()
	}
	switch {
	case err != nil:
		return err
	case auditErr != nil:
		return fmt.Errorf("token %s is revoked, but %w", id, auditErr)
	}
	fmt.Println("revoked", id)
	return nil
}

// tokenEuid is swapped by a test to play root.
var tokenEuid = os.Geteuid

// refuseAnotherUsersState refuses a token command run as root (`sudo
// flyball token ...`) against a front whose state directory -- or, when
// that does not exist yet, its nearest existing ancestor -- belongs to
// another user, or holds a tokens file or audit that does. Every file the
// command would create there (the directory, tokens.json, rewritten each
// time through a temp file and a rename, its .lock, audit.jsonl) would be
// root's, 0600: that front could then no longer read its tokens or write
// its audit, and it refuses sign-ins without one. Refusing is the choice
// over creating as root and chowning afterwards, which would have to
// follow the tokens store's own temp-and-rename writes.
func refuseAnotherUsersState(tokensPath string) error {
	if tokenEuid() != 0 {
		return nil
	}
	dir := filepath.Dir(tokensPath)
	check := []string{filepath.Join(dir, filepath.Base(tokensPath)), filepath.Join(dir, frontwire.AuditFile)}
	for p := dir; ; p = filepath.Dir(p) {
		if _, err := os.Lstat(p); err == nil {
			check = append(check, p)
			break
		} else if !errors.Is(err, fs.ErrNotExist) {
			return err
		}
		if filepath.Dir(p) == p {
			break
		}
	}
	for _, p := range check {
		fi, err := os.Lstat(p)
		if err != nil {
			continue
		}
		st, ok := fi.Sys().(*syscall.Stat_t)
		if !ok || st.Uid == 0 {
			continue
		}
		owner := strconv.FormatUint(uint64(st.Uid), 10)
		if u, err := user.LookupId(owner); err == nil {
			owner = u.Username
		}
		return fmt.Errorf("this runs as root, but %s belongs to %s: a file made here now would be root's,"+
			" and that front could no longer read its tokens or write its audit (it then refuses sign-ins);"+
			" run it as the front's user: `sudo -u %s flyball token ...`", p, owner, owner)
	}
	return nil
}

// subCLI is who a token change made by this command is recorded as, in
// the front's audit (beside local:console and local:admin, auth.go).
const subCLI = "local:cli"

// openTokenAudit opens the audit of the front whose tokens file is
// tokensPath: frontwire.AuditFile beside it, the file that front appends
// to (frontwire.OpenAudit). Appending from here while that front runs is
// safe: each holds its own O_APPEND descriptor and writes one whole record
// per write(2) (slog's handler), which the kernel appends at the end of
// the file without interleaving another's -- on a local filesystem, not
// NFS. The sequence numbers are this command's own, under its own boot id.
func openTokenAudit(tokensPath string) (*front.Audit, error) {
	path := filepath.Join(filepath.Dir(tokensPath), frontwire.AuditFile)
	audit, err := front.OpenAudit(path)
	if err != nil {
		return nil, fmt.Errorf("the front's audit log %s cannot be opened (%v)", path, errors.Unwrap(err))
	}
	return audit, nil
}

// addReadScopes adds read on the same rig for every non-read, non-management
// scope. The vocabulary is an unordered set of verbs until D-034 lands, so
// an elevated scope such as operate:blender does not itself imply read:
// without this, a CLI-issued operate token gets 403 "needs 'read'" on a
// websocket. CLI-only (flyball token create, flyball login --scope): the
// server and the verb table are unchanged.
func addReadScopes(scopes []string) []string {
	out := append([]string{}, scopes...)
	for _, s := range scopes {
		p, err := grants.ParseScope(s)
		if err != nil || p.Management() || p.Verb == grants.Read {
			continue
		}
		out = append(out, grants.Read+":"+p.Rig)
	}
	return grants.Normalize(out)
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

// popSets collects every --set VALUE and --set=VALUE (order preserved),
// returning them and the remaining args.
func popSets(args []string) ([]string, []string) {
	var sets []string
	out := make([]string, 0, len(args))
	for i := 0; i < len(args); i++ {
		if v, ok := strings.CutPrefix(args[i], "--set="); ok {
			sets = append(sets, v)
			continue
		}
		if args[i] == "--set" && i+1 < len(args) {
			sets = append(sets, args[i+1])
			i++
			continue
		}
		out = append(out, args[i])
	}
	return sets, out
}

// parseExpires is store.ParseDuration (Go durations plus a whole-days form,
// "30d") with --expires-shaped errors, matching auth.md's examples
// (`--expires 30d`).
func parseExpires(s string) (time.Duration, error) {
	d, err := store.ParseDuration(s)
	if err != nil {
		return 0, fmt.Errorf("--expires %q: %w (also accepts a whole number of days, e.g. 30d)", s, err)
	}
	if d < 0 {
		return 0, fmt.Errorf("--expires %q: a token's lifetime must be positive", s)
	}
	return d, nil
}

// lifetimesFor reads config's tokens: block -- `runner.front.tokens` for a
// rig file, flyballd.yaml's top-level `tokens:` for a daemon config -- and
// resolves it the same way the front does (store.ResolveLifetimes), so
// that an offline `flyball token create --config PATH` applies the same
// effective default/max lifetimes a running front would.
func lifetimesFor(configs, sets []string, forceDaemon bool) (store.Lifetimes, []string, error) {
	tc, err := tokensConfigFor(configs, sets, forceDaemon)
	if err != nil {
		return store.Lifetimes{}, nil, err
	}
	var defaultLifetime, maxLifetime string
	if tc != nil {
		defaultLifetime, maxLifetime = tc.DefaultLifetime, tc.MaxLifetime
	}
	lifetimes, warnings := store.ResolveLifetimes(defaultLifetime, maxLifetime)
	return lifetimes, warnings, nil
}

// tokensConfigFor reads the tokens: block without validating it
// (store.ResolveLifetimes does that): the top level for a daemon config
// (one file, no --set), or runner.front.tokens for rig files, every file
// and --set merged as `flyball run` merges them (D-046). A missing single
// file or block is nil, nil -- the same "not set" lifetimesFor treats as
// the built-ins.
func tokensConfigFor(configs, sets []string, forceDaemon bool) (*front.TokensConfig, error) {
	config := configs[0]
	daemon, err := isDaemonConfig(config, forceDaemon)
	if err != nil {
		return nil, err
	}
	if daemon && (len(configs) > 1 || len(sets) > 0) {
		return nil, fmt.Errorf("%s is flyballd's config: a second --config or a --set applies to rig files only", config)
	}
	data, err := os.ReadFile(config)
	if err != nil {
		if os.IsNotExist(err) && len(configs) == 1 && len(sets) == 0 {
			return nil, nil
		}
		return nil, fmt.Errorf("reading %s: %w", config, err)
	}
	if daemon {
		var cfg struct {
			Tokens *front.TokensConfig `yaml:"tokens"`
		}
		if err := yaml.Unmarshal(data, &cfg); err != nil {
			return nil, fmt.Errorf("parsing %s: %w", config, err)
		}
		return cfg.Tokens, nil
	}
	// Rig files: extends resolved, layered and --set, as the front reads
	// them (rigDocument).
	document, err := rigDocument(configs, sets)
	if err != nil {
		return nil, fmt.Errorf("parsing %s: %w", strings.Join(configs, ", "), err)
	}
	runner, _ := document["runner"].(map[string]any)
	frontBlock, _ := runner["front"].(map[string]any)
	block, ok := frontBlock["tokens"]
	if !ok || block == nil {
		return nil, nil
	}
	raw, err := yaml.Marshal(block)
	if err != nil {
		return nil, fmt.Errorf("parsing %s: %w", config, err)
	}
	var tc front.TokensConfig
	if err := yaml.Unmarshal(raw, &tc); err != nil {
		return nil, fmt.Errorf("parsing %s: runner.front.tokens: %w", config, err)
	}
	return &tc, nil
}

// tokensPathFor is where --config PATH's tokens.json lives (§WP0-4), using
// the same directories the two fronts themselves use
// (daemon/internal/frontwire), so a running front and this offline command
// always agree on the file:
//
//   - PATH is flyballd.yaml (--daemon, the file's name, or one of its own
//     top-level keys -- see isDaemonConfig) -> its data_dir as
//     config.LoadDaemonConfig resolves it (a relative one under the
//     file's directory; $STATE_DIRECTORY or /var/lib/flyball when unset)
//     -> frontwire.DaemonDir(dataDir), the same absolute directory
//     `flyballd --config flyballd.yaml` opens its tokens file in,
//     whatever either process's cwd is;
//   - otherwise PATH is a rig file -> frontwire.RunDir(id), id =
//     frontdir.FrontID(the absolute rig path), the same id and
//     $XDG_STATE_HOME-relative directory `flyball run` uses for its
//     front.
//
// This mirrors flyballd's own `--config` flag (cmd/flyballd/main.go) by
// name and shape deliberately: `flyball token create --config
// flyballd.yaml` bootstraps a daemon-fronted token exactly the way
// `flyballd --config flyballd.yaml` starts that same daemon.
func tokensPathFor(config string, forceDaemon bool) (string, error) {
	daemon, err := isDaemonConfig(config, forceDaemon)
	if err != nil {
		return "", err
	}
	if daemon {
		if _, err := os.Stat(config); err != nil {
			return "", fmt.Errorf("reading %s: %w", config, err)
		}
		cfg, err := daemonconfig.LoadDaemonConfig(config)
		if err != nil {
			return "", err
		}
		return filepath.Join(frontwire.DaemonDir(cfg.DataDir), frontwire.TokensFile), nil
	}
	id, err := frontdir.FrontID(config)
	if err != nil {
		return "", fmt.Errorf("%s: %w", config, err)
	}
	state, err := frontwire.RunDir(id)
	if err != nil {
		return "", err
	}
	return filepath.Join(state, frontwire.TokensFile), nil
}

// daemonOnlyKeys are flyballd.yaml's own top-level keys
// (config.daemonKeys, duplicated here: cmd/flyball does not import the
// internal/config package). None is required, so a file with any one of
// them, but none of a rig file's keys such as devices or runner, is a
// daemon config; a rig file has none of them at its top level.
var daemonOnlyKeys = []string{"manifests_dir", "data_dir", "default_server", "log_max_size"}

// isDaemonConfig says whether config is flyballd.yaml rather than a rig
// file: --daemon says so; else a file named flyballd.yaml (or .yml) is;
// else one with any of daemonOnlyKeys at its top level is. A flyballd.yaml
// that sets only the front's keys (listen, auth, password, ...) has none of
// daemonOnlyKeys, so under another name it needs --daemon.
func isDaemonConfig(config string, forced bool) (bool, error) {
	if forced {
		return true, nil
	}
	switch filepath.Base(config) {
	case "flyballd.yaml", "flyballd.yml":
		return true, nil
	}
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
	for _, k := range daemonOnlyKeys {
		if _, has := top[k]; has {
			return true, nil
		}
	}
	return false, nil
}
