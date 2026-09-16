# flyball UI

A library first, an app second. Nothing here knows what a rig measures: every
panel is a function of the JSON the daemon publishes (see the book's *HTTP and
websocket API* and *Wire format* pages), and the app is the smallest consumer
of the library.

```
packages/client   @flyball/client   wire types, Transport interface, RigClient, schema helpers. No framework.
packages/react    @flyball/react    RigProvider + hooks (the only place fetching happens), SchemaForm, panels.
apps/dashboard    @flyball/dashboard  the served app: provider, rig document, one panel per actuator.
```

## Run

```bash
cd examples/humidity && uv run humidity-sim        # a rig with nothing plugged in, on :8000
cd ui && npm install && npm run dev                # Vite on :5173, /api and /ws proxied to :8000
```

`FLYBALL_URL=http://pi:8000 npm run dev` points the proxy elsewhere.

## Use as a library

```tsx
import { RigProvider, useRigSchema, useActuatorStates, useCommands, ActuatorPanel } from "@flyball/react";
import "@flyball/react/styles.css";

function Pumps() {
  const schema = useRigSchema();                       // GET /api/schema
  const { states } = useActuatorStates();              // /ws/actuators
  const commands = useCommands("actuators", "pumps");  // POST /api/actuators/pumps/{command}
  const pumps = schema.data?.actuators.pumps;
  return pumps ? <ActuatorPanel schema={pumps} state={states.pumps} onRun={commands.run} busy={commands.busy} results={commands.results} /> : null;
}

<RigProvider url="http://pi:8000"><Pumps /></RigProvider>
```

The rules that keep it embeddable:

- **Panels take data and emit events.** `ActuatorPanel`, `CommandForm`, `StateView`, `SchemaForm` never fetch; they render props and call callbacks. They work against a mock, a recording, or someone else's server.
- **Hooks fetch.** `useRigSchema`, `useDeviceView`, `useActuatorStates`, `useStream`, `useCommands`. Each returns the same `{data, error, loading, refresh}` shape, so they can be swapped for TanStack Query later without touching a panel.
- **Live values go through one store.** `RigProvider` owns a `TelemetryStore`: ring buffers (`Float64Array`) per channel and per loop, the latest actuator states, a capped event ring, and the four sockets (`samples`, `loops`, `actuators`, `events`), opened on the first subscriber and closed five seconds after the last. Read it with `useLatest(key)` (one value, ≤ 4 Hz, re-renders only the caller), `useTraceRef(channels)` (a handle a chart draws from via its `source` prop, ≤ 10 Hz `setData`, no React re-render on samples), `useLoopLatest`, `useActuatorState`, `useEventsFeed`, `useStreamStatus`, `useFreshness`. `useSamples`/`useLoops`/`useActuatorStates`/`useEvents` remain as adapters over the store for panels that take arrays.
- **Transport is an interface.** `RigProvider` takes a `transport` prop; `browserTransport` (fetch + WebSocket with reconnect) is the default. A test passes a fake.
- **No global state, no router, no leaking CSS.** Styles are scoped under `.fb-*` and driven by CSS variables (`--fb-accent`, …).

## Theming

Every colour, space, radius, shadow and duration is a CSS custom property on `:root`,
defined once in `packages/react/src/styles.css` (`ui/DESIGN-SPEC.md` §1.1 is the spec
they implement). The app never invents a colour: `apps/dashboard/src/theme.tsx`'s
`makeTheme(mode)` **reads** these tokens with `getComputedStyle` and hands them to MUI's
palette, so an embedder with no MUI at all gets the same look from the stylesheet alone.

- **Light is the base** (`:root`). **Dark** overrides live under both
  `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }` (follows the
  OS) and `:root[data-theme="dark"] {…}` (the explicit toggle wins over the OS in both
  directions). `AppTheme` sets `data-theme` on `<html>` from `flyball.theme` in `localStorage`.
- **Density** (`comfortable`/`compact`) is `data-density` on `<html>`, from `flyball.density`;
  it changes only `--fb-gap`, a tile's title-row height and a readout's minimum height
  (`:root[data-density="compact"]` in `styles.css`).
- **Reduced motion** collapses `--fb-dur-*` to `0ms` (`@media (prefers-reduced-motion: reduce)`).
- `$S/tools/contrast.mjs` (a session scratchpad script; not part of the repo) recomputes WCAG
  contrast for every text/surface pair straight from `styles.css` — re-run it after changing
  any colour token.

| Token | Purpose |
|---|---|
| `--fb-bg-0` | Canvas: the page background behind every panel. |
| `--fb-bg-1` | Panel / card surface. |
| `--fb-bg-2` | Inset: readout tiles inside a panel, table stripes, inputs. |
| `--fb-bg-3` | Elevated: menus, dialogs, drawers. |
| `--fb-bg-hover` | Hover fill on a clickable card/row. |
| `--fb-bg-selected` | Selected-row / selected-widget fill. |
| `--fb-border-1` | Weak border: card edges, dividers. |
| `--fb-border-2` | Medium border: inputs, hover state. |
| `--fb-border-3` | Strong border: focus-adjacent, drag handles. |
| `--fb-fg` | Primary text. |
| `--fb-fg-2` | Secondary text (labels, captions, muted values). |
| `--fb-fg-3` | Tertiary text — 12px and up only, never a value. |
| `--fb-fg-disabled` | Disabled text/icon. |
| `--fb-fg-on-accent` | Text/icon drawn on a filled accent surface. |
| `--fb-accent` | Primary accent (links, focus ring, regulating mode, range fill). |
| `--fb-accent-hover` | Accent on hover. |
| `--fb-accent-soft` | Accent tint for a selected/soft-filled surface. |
| `--fb-ok` | Positive **transitions** only (recording started, program done) — never a steady "healthy" state. |
| `--fb-warn` / `--fb-warn-fill` | Warning text/border, and its soft fill for badges/bands. |
| `--fb-alarm` / `--fb-alarm-fill` | Alarm text/border, and its soft fill. |
| `--fb-ok-fill` | Soft fill for a positive band/badge. |
| `--fb-stale` | Stale/disconnected — always paired with a dashed border, never colour alone. |
| `--fb-info` | Alias of `--fb-accent` for informational (non-severity) callouts. |
| `--fb-series-1`…`--fb-series-8` | The 8-slot chart palette, fixed order per entity, colour-blind-validated (§1.2). Never re-cycled when a series is hidden. |
| `--fb-series-setpoint` | Setpoint/reference overlay — neutral + dashed, not a series slot. |
| `--fb-series-band` | Normal-band shading behind a trace. |
| `--fb-grid` | Chart gridlines. |
| `--fb-axis` | Chart axis labels. |
| `--fb-cursor` | Chart hover cursor line. |
| `--fb-radius-1` / `-2` / `-pill` | 4px controls/inputs/chips, 6px cards/widgets, pill status chips. |
| `--fb-space-1`…`-6` | The spacing scale: 4/8/12/16/24/32px. |
| `--fb-gap` | Alias of `--fb-space-3` (12px) — most existing rules read this name. |
| `--fb-radius` | Alias of `--fb-radius-2`. |
| `--fb-shadow-1`…`-3` | Elevation shadows (subtle → overlay → chart-expanded). |
| `--fb-focus` | The one focus ring: `box-shadow` value for `:focus-visible`. |
| `--fb-dur-1`…`-3` | Motion durations: 120/180/240ms; zeroed under reduced motion. |
| `--fb-ease` / `--fb-ease-out` | The two eases routine UI motion uses. |
| `--fb-font` / `--fb-font-mono` | System font stack; mono for raw JSON/YAML and event details. |
| `--fb-text-xs`…`-2xl` | Type scale: 11/12/13/14/16/20px. |
| `--fb-chart-min-h` | Density's floor for a chart's height clamp — declared for `panels/{MultiSeries,TimeSeries}.tsx` to read; **not yet wired in** (they still hard-code their clamp). |
| `--fb-bg`, `--fb-panel`, `--fb-border`, `--fb-muted`, `--fb-error` | Legacy names from before this token set (§1.1): alias `bg-2`, `bg-1`, `border-1`, `fg-2`, `alarm` respectively so old rules keep resolving while they migrate. |
| `--fb-warning`, `--fb-warning-fg`, `--fb-error-bg`, `--fb-error-fg` | Older badge aliases (`fb-mode-open`, `fb-event-level`): alias `warn-fill`/`warn`/`alarm-fill`/`alarm`. |

## Performance

Measured 16 Sep 2026 with `perf.mjs` (headless Chromium via Playwright, Vite dev server, React Strict Mode on -- development builds, so absolute numbers are pessimistic; relative ones hold) on a Ryzen 9 5950X. Each row is one page held for N seconds after a 4 s settle; TaskDuration is CDP `Performance.getMetrics`, long tasks are `PerformanceObserver('longtask')`, App renders is `window.__fb.renders`. Rigs: `examples/simulated/furnace.toml` (×60 clock, `anneal` running), `examples/stress/plant.toml` (41 channels, 17 loops, `plant-firing`), `examples/stress/torrent.toml` (~11 600 samples/s).

| rig | route | s | TaskDuration before → after | long tasks before → after | App renders before → after | heap after (start → end) | console errors + warnings before → after |
|---|---|---|---|---|---|---|---|
| furnace | `#/` | 30 | 25.8 s → **2.4 s** | 0 → 0 | 2350 → 0 | 17.4 → 35.6 MB | 26+8 → 0+0 |
| furnace | `#/dashboards` | 10 | 7.4 s → **1.2 s** | 0 → 0 | 1250 → 0 | 24.3 → 37.4 MB | 12+4 → 0+0 |
| furnace | `#/loops` | 10 | 6.3 s → **0.5 s** | 0 → 0 | 1242 → 0 | 31.4 → 18.3 MB | 12+4 → 0+0 |
| plant | `#/` | 10 | 7.7 s → **3.3 s** | 55 → 8 | 112 → 0 | 57.6 → 24.9 MB | 13+4 → 0+0 |
| plant | `#/dashboards` | 10 | 9.2 s → **3.9 s** | 53 → 0 | 332 → 0 | 108.8 → 201.1 MB | 19+4 → 6+0 |
| plant | `#/loops` | 10 | 8.5 s → **2.1 s** | 9 → 0 | 374 → 0 | 66 → 73.6 MB | 13+4 → 0+0 |
| torrent | `#/` | 62 | 45.9 s → **11.6 s** | 158 → 0 | 194 → 0 | 22.9 → 40.2 MB | 36+4 → 1+0 |
| torrent | `#/dashboards` | 10 | 6.4 s → **2.0 s** | 3 → 0 | 522 → 0 | 26.3 → 30.6 MB | 12+4 → 0+0 |
| torrent | `#/loops` | 10 | 8.6 s → **1.3 s** | 5 → 0 | 1078 → 0 | 14.3 → 18.9 MB | 12+4 → 0+0 |

Heap columns are raw `JSHeapUsedSize` and move with the collector's timing; with a forced collection before each reading (`perf.mjs --gc`) the torrent overview holds 15.5 → 16.4 MB over 60 s (+6 %). The plant `#/dashboards` errors after are six duplicate-key warnings from the generated overview (`chart-C`/`chart-m` widget ids collide), not telemetry. The plant `#/` long tasks after are the page's own re-render on the 5 s health poll (MUI `sx` styling in a development build, ~60 ms with Strict Mode's double render); a production build is well under 50 ms.

What changed: samples used to be folded into React state at the app root (`useSamples` in `App`), so every sample re-rendered the whole tree and rebuilt every trace array; they now land in a `TelemetryStore` (§6 of `DESIGN-SPEC.md`) and charts draw from it directly. `/ws/readers` is folded once a second. A chart off screen (`IntersectionObserver`, 200 px margin) or in a hidden tab does no `setData` and gets one when it returns; a 12-chart dashboard scrolled so 6 are off screen redraws only the visible 6 (`window.__fb.chartsById`).

## Build

`npm run typecheck` (project references across all three), `npm run build` (packages emit `dist/` with `.d.ts`; the app emits `apps/dashboard/dist`), `npm test` (vitest; `packages/react/test` covers the ring buffer and the store). The `development` export condition resolves packages to source under Vite, so there is no build step in dev.

## Next

In the order of `UI.md` §6: graph panel (uPlot, `/ws/samples` + `/api/history/…/series`), the loop panel, layouts. The `/ws/samples` messages carry no `source` name yet — needed before a second source exists.
