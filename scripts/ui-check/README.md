# ui-check — headless verification tooling

Scripts used on 16 Sep 2026 to verify the UI against every example rig. All headless (Playwright's
`chromium`), never the interactive browser. Paths to Playwright are hard-coded for this machine
(`~/.npm/_npx/…/playwright-core`, `~/.cache/ms-playwright/chromium-1169`); adjust the two constants at
the top of each `.mjs` if they move. Working files go to `$FLYBALL_CHECK_DIR` (default `/tmp/flyball-check`).

| script | does |
| --- | --- |
| `rig-up.sh <rig file> <api> <ui> [--record …]` | runner (store under the check dir) + Vite dev server proxied to it; logs and PIDs under the check dir |
| `rig-down.sh <rigname>` | kills both process groups |
| `shot.mjs <url> <out.png> [--width --height --wait --dark --full --click sel]` | screenshot + `<out>.log` of every console error/warning; prints counts. The number to watch: every page on every rig should print `errors=0 warnings=0` |
| `measure.mjs <url> <selector>` | bounding boxes and canvas sizes of matching elements (chart plot area checks) |
| `perf.mjs <ui-url> '#/route' [seconds] [--gc --json --scroll]` | CDP TaskDuration, long tasks, heap, `window.__fb` render/redraw counters |
| `dash-e2e.mjs <ui-url> <api-url>` | 22-step dashboard editor test (add/bind/resize/undo/redo/configure/duplicate/remove/save/reload/rename/export/import/home/delete, no RGL in view mode, zero console issues); must print `ALL PASS` |
| `passkey-e2e.mjs <ui-url> <password> [--shots]` | **Phase 3 only: needs passkeys in the Go front, and does not run against Phase 1** (nothing serves `/api/auth/passkey/*`, and the passkey UI stays hidden while `login.passkey` is false). WebAuthn passkey login via Playwright's CDP virtual authenticator: password login to bootstrap, register/list/revoke a passkey through the real UI, sign in with the passkey alone, and a negative case (login attempt with no registered passkey refused client-side, no stray session); `<ui-url>` **must** be `http://localhost:<port>` (see note below); must print `ALL PASS` |
| `contrast.mjs` | WCAG contrast of every `--fb-*` fg/bg pair in `packages/react/src/styles.css`, both modes |
| `programForm-roundtrip.mjs <api-url>` | text ⇄ builder-form fidelity for every example program against the runner's rig, a rig-less model and an empty rig |
| `programText-roundtrip.mjs` | parses every example program with the app's `programText.ts`, round-trips yaml/toml/json, cross-checks PyYAML |
| `sweep.sh <rig file> <api> <ui>` | one rig through every page at 1440 light, dark, 400 px, plus a channel and a loop detail page |
| `auth-e2e.mjs --flyball <binary> [--shots <dir>] [--only password\|none\|starting\|local\|bare]` | the door end to end against real processes it starts and stops itself (`flyball run` on port 0 with private XDG dirs in a temp dir; a bare `flyball-runner` on a port in 18470-18499): the password shape (anonymous read-only, wrong password refused, sign in with an HttpOnly cookie and nothing in localStorage, the stop button only with operate and its interim report, sign out closing the sockets, a session revoked from outside turning the page read-only with no reload loop), `anonymous: none` (the login page, and back to it on revocation), a page opened while the runner starts, the local shape, and the bare runner's one-time link; must print `ALL PASS` with `errors=0 warnings=0 pageerrors=0`. Needs a `flyball` built with the UI: see below |
| `events-e2e.mjs <ui-url> <api-url> [--shots]` | proactive event notifications end to end, against `examples/simulated/furnace.yaml`: a real WARNING+/ERROR event (via the furnace's own `fail`/`restore` simulation commands) toasts while off the Events page, the nav badge tracks the unread count, dismissing a toast marks it read, "Mark all read" and revisiting the page clear the badge; must print `ALL PASS` |

**`passkey-e2e.mjs` and `localhost`.** (For Phase 3, once the Go front serves passkeys.) WebAuthn's secure-context/RP-ID rules need the page served
as `http://localhost:<port>` specifically, not `127.0.0.1` -- confirmed by driving the real ceremony
both ways. `rig-up.sh`'s vite server listens on `localhost` already (no `--host` override), so it's
reachable at either; just navigate Playwright to the `localhost` form when running this script.

**`rig-up.sh` and worktrees.** It hard-codes `cd /home/ben/flyball/ui` before starting vite, so from
a worktree it silently serves the *main* repo's UI, not the worktree's -- confirmed the hard way (a
passkey button that should have existed didn't, because the served bundle predated it). Testing UI
changes that only exist in a worktree needs the vite server started by hand from that worktree's
`ui/` directory, pointed at the runner with `FLYBALL_URL`, following the same invocation `rig-up.sh`
uses.

Typical session:

```bash
export FLYBALL_CHECK_DIR=/tmp/flyball-check
scripts/ui-check/rig-up.sh examples/simulated/furnace.yaml 8001 5201 --record
curl -X POST localhost:8001/api/programs/library/anneal/run
node scripts/ui-check/shot.mjs http://127.0.0.1:5201/#/loops $FLYBALL_CHECK_DIR/shots/loops.png
node scripts/ui-check/dash-e2e.mjs http://127.0.0.1:5201 http://127.0.0.1:8001
scripts/ui-check/rig-down.sh furnace
```

## Gotcha: `rig-up.sh` in a worktree

`rig-up.sh` hard-codes `cd /home/ben/flyball/engine` and `cd /home/ben/flyball/ui` before
launching the runner and the Vite dev server. Run from a git worktree (`.claude/worktrees/…`),
it silently serves the **main repo's** engine/UI, not the worktree's -- any code only committed
or edited in the worktree is invisible to the check. Not patched (shared infra other work may
depend on the current behaviour); work around it by starting the runner and Vite by hand from the
worktree's own `engine/` and `ui/` instead of `rig-up.sh`, e.g.:

```bash
S=/tmp/flyball-check; mkdir -p "$S/logs" "$S/stores"
rig=$(realpath examples/simulated/furnace.yaml)   # from the worktree root
(cd engine && setsid nohup uv run --extra web --extra cli flyball-runner "$rig" --port 8091 \
  --store "$S/stores/furnace.sqlite" > "$S/logs/furnace-runner.log" 2>&1 &)
(cd ui && FLYBALL_URL="http://127.0.0.1:8091" setsid nohup npx vite \
  --config apps/dashboard/vite.config.ts apps/dashboard --port 5291 --strictPort \
  > "$S/logs/furnace-vite.log" 2>&1 &)
```

`rig-down.sh` still works to tear these down by rig name. Confirm you actually killed the prior
runner before assuming a store is empty -- `kill $(cat pids)` can miss the `uv run` wrapper's
child; `fuser -k <port>/tcp` and a `ss -ltnp | grep <port>` check are more reliable.

## `auth-e2e.mjs`: a flyball binary with the UI

The front serves the dashboard embedded in the Go binary (`daemon/internal/webui`, `go:embed`); a plain
`go build` embeds only a placeholder page, which the front refuses to pass off as the app. Either:

```bash
daemon/build-with-ui.sh -o /tmp/flyball-ui      # builds ui/, copies its dist into daemon/internal/webui/dist (in the tree)
```

or, leaving the tree untouched, the same steps in a copy of `daemon/`:

```bash
(cd ui && npm ci && npm run build)
d=$(mktemp -d) && cp -r daemon "$d/" && rm -rf "$d/daemon/internal/webui/dist/"* \
  && cp -r ui/apps/dashboard/dist/. "$d/daemon/internal/webui/dist/" \
  && (cd "$d/daemon" && go build -o /tmp/flyball-ui ./cmd/flyball) && rm -rf "$d"
```

Then, from the repository root, with `engine/.venv` synced (`cd engine && UV_FROZEN=1 uv sync --all-extras`):

```bash
node scripts/ui-check/auth-e2e.mjs --flyball /tmp/flyball-ui --shots /tmp/flyball-check/auth-shots
```

`CHROMIUM_PATH` picks the browser if playwright-core's own is not installed (for instance
`~/.cache/ms-playwright/chromium-1169/chrome-linux/chrome`). The one console line the script expects
and does not count is the 401 of its own wrong-password attempt; in the `starting` phase, the front's
503s while the runner starts.
