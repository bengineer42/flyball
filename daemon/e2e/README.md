# daemon/e2e: the front, end to end, with real processes

These tests start the real thing: the `flyball` and `flyballd` binaries built from this tree
(`TestMain` runs `go build` into a temp dir), the real `flyball-runner` from `engine/.venv`, real
unix sockets, TLS from a generated certificate, and a stand-in authenticating proxy. Nothing is
mocked. They sit behind the `e2e` build tag, so `go test ./...` skips them.

```bash
cd engine && UV_FROZEN=1 uv sync --all-extras       # once: the runner the tests start
cd daemon && UV_FROZEN=1 go test -tags e2e -race ./e2e/...
```

Without `engine/.venv/bin/flyball-runner` every test skips and says why. The whole package takes
about 15 s; each test runs in parallel with its own world.

## How a test is built

- **One temp dir per test** (`newEnv`): a short path under `$TMPDIR` holding the rig files (copies
  of `examples/simulated/oven.yaml` with a `runner:` section appended), their stores, a private
  `XDG_RUNTIME_DIR` (the front-dirs and sockets), `XDG_STATE_HOME` (tokens and the front's audit),
  `XDG_CONFIG_HOME` and `HOME`.
- **Ports:** every front listens on port 0 (the tests read the address `flyball run` or `flyballd`
  prints) or on a unix socket. Nothing uses a fixed port.
- **Teardown, pass or fail:** each process runs in its own process group and gets SIGTERM, then
  SIGKILL. Every process the test started carries an `E2E_MARKER` environment variable, and a last
  sweep of `/proc` kills anything still carrying it, such as a runner orphaned by a killed front.
  Then the temp dir is removed.
- **Output:** each process writes to a file in the temp dir, not a pipe, so a runner that outlives
  its front neither blocks the test nor loses its output. A failing test prints the tail of each.
- **The runner's side** is read directly: the front-dir files (`key`, `aud`, `runner.lock`), the
  store's `audit` table (through the venv's Python), and `/proc/<pid>/{cmdline,environ}`.

| test | what runs |
| --- | --- |
| `TestFrontDirWithoutKeyExits4` | `flyball-runner --front-dir` on a dir with no key, and with a short one |
| `TestRunLocal` | `flyball run`, local shape |
| `TestRunPassword` | `flyball run`, password shape with `anonymous: read`; named tokens over HTTP and by `flyball token create`; MCP |
| `TestRunSessionExpiry` | `flyball run`, password shape with `session: 2s` |
| `TestRunTLS` | `flyball run`, password shape with `tls:` from a generated certificate |
| `TestRunProxy` | `flyball run`, proxy shape (`authelia`) on a unix socket behind a stand-in proxy; the same preset on TCP with an unvouched peer |
| `TestRunFallbacks` | `flyball run` with each front misconfiguration, and with the runner's removed flags |
| `TestRunCleartextWarning` | `flyball run`, password shape on `0.0.0.0:0` without TLS |
| `TestRunRigEditRestarts` | `flyball run`, local shape: a rig edit (`POST /api/devices`) through the front, the runner restarting itself by execv (D-051) |
| `TestRunOldRunner` | `flyball run` with a stand-in runner that ignores the principal, and one that refuses `--front-dir` |
| `TestDaemonTwoRigs` | `flyballd` (password shape) with rigs `a` and `b`, an overlapping `c`, then a second `flyballd` over the same manifests |

The browser side is `scripts/ui-check/auth-e2e.mjs` (see `scripts/ui-check/README.md`).

## Phase 1 merge requirements

Each requirement, and the test here that proves it. "Unit" names where the rest is proved when an
end-to-end test cannot reach it.

| # | requirement (short) | proved by | not covered here, and why |
| --- | --- | --- | --- |
| 1 | `--front-dir` without a usable key exits 4 before hardware; a crash-restarted runner gets a fresh key | `TestFrontDirWithoutKeyExits4` (exit 4, no `<store>.lock`, no socket); `TestRunLocal/kill_-9,_respawn,_200`; `TestDaemonTwoRigs/kill_-9_on_a_runner:_respawn,_fresh_key,_200` | |
| 2 | Fronted, the principal is the only credential; bearer, `?token=`, cookies, rig-file `auth` and `anonymous` ignored | `TestRunLocal/the_socket_takes_only_a_good_principal`: the rig file sets `runner.auth.token` and `anonymous: read`; with that token as bearer, a cookie and `?token=` but no principal, the socket answers 401 | |
| 3 | Bad MAC, foreign `aud`, expiry, unknown version refused; A's principal replayed at B refused | `TestRunLocal/the_socket_takes_only_a_good_principal` (`mac`, `aud`, `expired`, `version`); `TestDaemonTwoRigs/a_principal_for_a_is_refused_at_b` | constant-time comparison is not observable from outside (unit: `principal`, A1) |
| 4 | `aud` and scopes from the same routed entry; overlapping root paths refused | `TestDaemonTwoRigs`: `aud` is the manifest name, a `read:a` token reaches `/a` and not `/b`, `c` at `/a/c` is refused at registration | |
| 5 | Front-dir 0700, owned, `lstat`-verified; another uid cannot connect; TCP only with `network: tcp` | `TestRunLocal/front-dir` (a 0700 directory, not a symlink; `key`/`aud`/`endpoint`/`runner.lock` 0600) | another uid needs a second account or root; `network: tcp` is not exercised (unit: `endpoint/frontdir`, `registry`, A2) |
| 6 | The key is never in argv, the environment or a log | `TestRunLocal/key_in_no_argv,_environment_or_log`; `TestDaemonTwoRigs/keys_in_no_argv,_environment_or_log;_runner_logs_0600` | |
| 7 | MCP inner calls re-minted, capped to caller ∩ tier; a read principal cannot reach a route above read | `TestRunPassword/MCP_through_the_front`: a read token at `/mcp/read` reads and is offered no actuating tool; at `/mcp/operate` it gets 403 and an audit row naming it; an operate token's `stop_rig` lands in the audit as `token:e2e-op` `via mcp` | "an operate-only principal cannot run an author tool" (unit: `test_mcp.py`, A6) |
| 8 | A Host outside the allow-list gets 403 | `TestRunLocal/host,_origin_and_path` (local shape) | the `url:` allow-list and the bare runner's Host rule (unit: `front` `TestHostRules`, `test_door.py`) |
| 9 | Acting with a missing, `null` or foreign Origin refused for cookie, local and anonymous callers, websockets included | `TestRunLocal/host,_origin_and_path` (local, POST and a websocket upgrade); `TestRunPassword/no_credential_in_a_URL;_the_session's_Origin_is_checked` (session; bearer exempt) | |
| 10 | Tri-state chain: a presented invalid credential is terminal; anonymous only when nothing was presented | `TestRunPassword/anonymous_read` (an unknown `fbt1_` token gets 401 although anonymous may read); `TestRunPassword/sign_out:…` (a stale cookie on a guarded route: 401) | JWKS/store errors as 503 (unit: `proxyauth`, `front`) |
| 11 | Logout, revocation, token expiry and session expiry close open websockets within 1 s | `TestRunPassword/token_revoked:…` (HTTP revoke), `/an_offline_token:…` (`flyball token revoke` in another process), `/sign_out:…`, `/token_expiry:…`; `TestRunSessionExpiry`. Each measures the close (4401) against 1 s | long HTTP streams (unit: `front` `TestRevocationClosesSockets`) |
| 12 | Effective token scopes = the ceiling ∩ the issuer's current verbs | | not reachable end to end in Phase 1: tokens are made only by the admin session, the local shape or the CLI, whose verbs are all verbs, so the intersection never narrows (unit: `front`, `store`) |
| 13 | `sid` not derivable from any cookie | `TestRunPassword/sign_in`: the session's `sid` in the runner's audit shares nothing with the cookie | |
| 14 | Password checks bounded, per-peer limited, fixed error string, delays; the password must be a `$scrypt$` line | `TestRunPassword/sign_in` (`{"detail":"Wrong password"}`); `TestRunFallbacks/plaintext_password` | the semaphore and the limiter (unit: `store` `TestHashSemaphore`, `TestLimiter`; `front` `TestLoginLimits`) |
| 15 | No default credential in any shape | `TestRunFallbacks/password_shape_with_no_password` (falls back to local on loopback; nothing is invented) | `examples/` carrying none (C3's `flyball rig check` sweep) |
| 16 | Only `/api`, `/ws`, `/mcp` proxied; `nosniff` and `CSP: sandbox`; the front's pages `frame-ancestors 'none'` | `TestRunLocal/headers` | |
| 17 | No credential in a URL but the bare runner's nonce; access logs drop queries; runner logs and the front's audit 0600 | `TestRunPassword/no_credential_in_a_URL;…` (`?token=` is anonymous at the front); `TestRunLocal/the_socket_takes_only_a_good_principal` (`?token=` at the runner); `TestRunLocal/flyball_stop_through_the_front` (no query in the access log); `TestRunPassword/the_front's_audit`; `TestDaemonTwoRigs/keys_in_no_argv,…` (runner logs 0600); `auth-e2e.mjs` phase `bare` (the nonce works once and leaves the address bar) | |
| 18 | Management needs the manage scope by bearer; a session never grants it | `TestDaemonTwoRigs/management_needs_the_manage_scope_by_bearer` (none 401, a rig token 403, the admin session 403 for list and restart, the manage token 200); `TestRunPassword/sign_in` (`manage` is refused over HTTP) | |
| 19 | Non-loopback credentials without TLS print the cleartext warning | `TestRunCleartextWarning` (at start and in `/api/auth`) | |
| 20 | Golden vectors pass in Go and Python | | a unit property of the two implementations (`principal` `TestVectors`, `test_principal_vectors`) |
| 21 | Every route has a verb row; an unmapped guarded route is refused at runtime | `TestRunLocal/headers` (an unknown `/api` path through the front gets 403 with every verb) | the route walk (unit: `test_verbs.py`) |
| 22 | No path-based decisions; `..`, `%2e`, `%2f`, `%5c` rejected before routing | `TestRunLocal/host,_origin_and_path` (raw request lines, 400) | |
| 23 | Forwarding by allow-list; underscore and non-canonical spellings dropped | `TestRunLocal/headers`: a client's `X-Flyball-Principal`, `X_Flyball_Principal`, `X-Flyball-Scopes`, `X-Forwarded-*`, `Forwarded` never reach the runner, which would refuse a second principal | the full list, header by header (unit: `front` `TestHeaderAllowList`) |
| 24 | Every misconfiguration removes exposure, never operation | `TestRunFallbacks`: plaintext password, `sso`, no password, bad TLS files, an unvouched unsigned proxy, non-loopback `local`, a `runner.front` that fails validation, an unknown shape, the runner's removed `--password`/`--session`; each serves local on loopback with a D-028 banner and the runner answers | |
| 25 | Stop needs operate, is never rate-limited, works with the front down (SIGUSR1); revocation never changes hardware state | `TestRunLocal/stop_over_HTTP` (interim report, audit row, a burst of 25 all 200); `/flyball_stop_through_the_front`; `/SIGUSR1_with_the_front_down` (front killed, `flyball stop --front-dir`, audit row `SIGNAL`/`SIGUSR1`, the runner lives); `TestRunPassword/a_read_token` (403 `needed: operate`); `/revoking_a_principal_leaves_the_program_running`; `auth-e2e.mjs` (the button only with operate) | |
| 26 | Refused upgrades complete and close 4401; a bad principal at the runner is 502 at the front; anonymous 403 becomes 401 | `TestRunPassword` (a revoked token's and a stale cookie's upgrade: 101 then 4401; an anonymous stop: 401) | runner-401 → 502 needs the front and runner to disagree on a key, which cannot be caused from outside (unit: `front` `TestRunner401Becomes502`) |
| 27 | A runner that does not accept `--front-dir` is never proxied to; readiness needs the handshake | `TestRunOldRunner`: a runner answering without a principal is only ever probed, and the front answers 502 "runner too old"; one refusing `--front-dir` ends the run saying so | |
| 28 | TLS certificates reload on renewal; a bad renewal keeps the last good pair | `TestRunTLS` (SIGHUP and the periodic re-stat each serve the new serial to new connections; garbage keeps serial 3) | |
| 29 | `check_driver` and `search_drivers` absent from the HTTP MCP | `TestRunPassword/MCP_through_the_front` (neither at `/mcp/read` nor `/mcp/operate`) | |
| 30 | Identities keyed by (issuer, subject); no path matches on email | `TestRunProxy` (`proxy:authelia#ben`; an email alone is anonymous; a grant naming an email grants nothing) | the signed presets (unit: `proxyauth`) |
| 31 | The action audit is append-only, outside retention, survives a session delete, never blocks a stop | `TestRunLocal/stop_over_HTTP` (UPDATE and DELETE on `audit` refused); every stop in these tests is found in it | retention, session delete and a failing write (unit: `test_audit.py`) |
| 32 | The placeholder UI is never served silently | `TestRunLocal/headers` (a binary built without the UI answers with the page naming `build-with-ui.sh`) | |
| 33 | Both books build strict; every verification command green | | not an end-to-end property: each package's own gate, and the docs package's |

### D1's own list

| item | test |
| --- | --- |
| 1 no key → exit 4; forged principal over the socket → 401 | `TestFrontDirWithoutKeyExits4`; `TestRunLocal/the_socket_takes_only_a_good_principal` |
| 2 `kill -9` under `flyballd`: fresh key, 200 | `TestDaemonTwoRigs/kill_-9_on_a_runner:…` |
| 3 `POST /api/runner/restart` (execv) through the front | `TestDaemonTwoRigs/POST_/api/runner/restart_(execv)_through_the_front` (same pid, a second start in its log, 200 after) |
| 4 MCP `/mcp/read` with a read token; an operate tool refused, audited with the token | `TestRunPassword/MCP_through_the_front` |
| 5 a unix-socket runner shows running | `TestDaemonTwoRigs/management_needs_the_manage_scope_by_bearer` (`running`, `unix:` endpoints) |
| 6 a second `flyballd` over the same manifests: exit 3, the first keeps the rig and its key | `TestDaemonTwoRigs/a_second_flyballd_over_the_same_manifests` (its runners `busy`; the first's pid and key unchanged, still 200) |
| 7 `flyball stop` with the front down (SIGUSR1) | `TestRunLocal/SIGUSR1_with_the_front_down` |
| 8 TLS, renewal | `TestRunTLS` |
| 9 proxy headers over a unix socket accepted, over TCP ignored | `TestRunProxy` |
| 10 the UI against the Go front with a real runner | `scripts/ui-check/auth-e2e.mjs` |

## Not here yet

- **flyballd restart and runner adoption (D-037)** are not in this suite; they are covered in
  `cmd/flyballd/adopt_test.go` against real `flyballd` processes: `TestSIGTERMLeavesTheRunnerRunning`,
  `TestSIGINTToFlyballdsGroupLeavesTheRunnerRunning`, `TestARestartedFlyballdAdoptsItsRunner`,
  `TestASIGKILLedFlyballdsRunnerIsAdopted`, `TestRealRunnerIsAdopted` (the real `flyball-runner`),
  `TestWithNoRuntimeDirTheRunnerIsNotAdopted`, and `flyball stop --all` / `flyball runners stop
  --all` in `TestStopAllsAgainstRealRunners`; at unit level in `internal/backend/adopt_test.go`.
- **A second uid.** Requirement 5's "another uid cannot connect" needs a second account.

## Fixed since D1

Two checks that D1 found failing are now green:

- `TestRunPassword/the_cookie_is_named_after_the_port_the_front_serves`: `(*Front).Bound`
  recomputes `cookieName` from the bound TCP port (`internal/front/auth.go`), called from
  `frontwire.Serve` once `front.Listen` knows it, so `--listen 127.0.0.1:0` no longer names
  every such front's cookie `flyball-0`.
- `auth-e2e.mjs` phase `starting`: a page opened before the runner answers now shows a
  non-alarming starting state and retries `GET /api/devices` on a backoff
  (`ui/apps/dashboard/src/useStartingRetry.ts`) instead of a permanent "Cannot reach the rig"
  alert.
