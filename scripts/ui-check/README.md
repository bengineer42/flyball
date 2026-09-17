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
| `contrast.mjs` | WCAG contrast of every `--fb-*` fg/bg pair in `packages/react/src/styles.css`, both modes |
| `programForm-roundtrip.mjs <api-url>` | text ⇄ builder-form fidelity for every example program against the runner's rig, a rig-less model and an empty rig |
| `programText-roundtrip.mjs` | parses every example program with the app's `programText.ts`, round-trips yaml/toml/json, cross-checks PyYAML |
| `sweep.sh <rig file> <api> <ui>` | one rig through every page at 1440 light, dark, 400 px, plus a channel and a loop detail page |

Typical session:

```bash
export FLYBALL_CHECK_DIR=/tmp/flyball-check
scripts/ui-check/rig-up.sh examples/simulated/furnace.yaml 8001 5201 --record
curl -X POST localhost:8001/api/programs/library/anneal/run
node scripts/ui-check/shot.mjs http://127.0.0.1:5201/#/loops $FLYBALL_CHECK_DIR/shots/loops.png
node scripts/ui-check/dash-e2e.mjs http://127.0.0.1:5201 http://127.0.0.1:8001
scripts/ui-check/rig-down.sh furnace
```
