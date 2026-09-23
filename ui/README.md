# flyball UI

A library first, an app second. Nothing here knows what a rig measures: every
panel is a function of the JSON the runner publishes (see the book's *HTTP and
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
import { RigProvider, useDevice, useDeviceSchema, useCommands, DevicePanel } from "@flyball/react";
import "@flyball/react/styles.css";

function Pumps() {
  const device = useDevice("pumps");                    // GET /api/devices/pumps
  const schema = useDeviceSchema("pumps");               // GET /api/devices/pumps/schema
  const commands = useCommands("pumps");                 // POST /api/devices/pumps/commands/{command}
  if (!device.data || !schema.data) return null;
  return <DevicePanel device={device.data} schema={schema.data} onRun={commands.run} busy={commands.busy} results={commands.results} />;
}

<RigProvider url="http://pi:8000"><Pumps /></RigProvider>
```

The rules that keep it embeddable:

- **Panels take data and emit events.** `DevicePanel`, `CommandForm`, `StateView`, `SchemaForm` never fetch; they render props and call callbacks. They work against a mock, a recording, or someone else's server.
- **Hooks fetch.** `useRigSchema`, `useDevices`, `useDevice`, `useDeviceSchema`, `useCommands`, `useRigDocument`. Each query hook returns the same `{data, error, loading, refresh}` shape, so they can be swapped for TanStack Query later without touching a panel.
- **Live values go through one store.** `RigProvider` owns a `TelemetryStore`: ring buffers (`Float64Array`) per signal, the latest write and controller state, a capped event ring, and the four sockets (`samples`, `controllers`, `activities`, `events`), opened on the first subscriber and closed five seconds after the last. Read it with `useSignal(address)`/`useLatestValue(address)` (one value, ≤ 4 Hz, re-renders only the caller), `useTraceRef(channels)` (a handle a chart draws from via its `source` prop, ≤ 10 Hz `setData`, no React re-render on samples), `useWriteState`, `useController`, `useDeviceRun`, `useActivityStates`, `useEventsFeed`, `useStreamStatus`, `useFreshness`.
- **Transport is an interface.** `RigProvider` takes a `transport` prop; `browserTransport` (fetch + WebSocket with reconnect) is the default. A test passes a fake.
- **No global state, no router, no leaking CSS.** Styles are scoped under `.fb-*` and driven by CSS variables (`--fb-accent`, …).

## Theming

Every colour, space, radius, shadow and duration is a CSS custom property on `:root`,
defined once in `packages/react/src/styles.css`. The app never invents a colour: `apps/dashboard/src/theme.tsx`'s
`makeTheme(mode)` **reads** these tokens with `getComputedStyle` and hands them to MUI's
palette, so an embedder with no MUI at all gets the same look from the stylesheet alone.

- **Light is the base** (`:root`). **Dark** overrides live under both
  `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {…} }` (follows the
  OS) and `:root[data-theme="dark"] {…}` (the explicit toggle wins over the OS in both
  directions). `AppTheme` sets `data-theme` on `<html>` from `flyball.theme` in `localStorage`.
- **Density** is one setting: `AppTheme` sets `data-density="comfortable"` on `<html>`, which
  `styles.css` and `widgets/size.ts` read for `--fb-gap`, a tile's title-row height and a
  readout's minimum height. There is no toggle and nothing stored; the compact rules
  (`:root[data-density="compact"]`) remain in `styles.css` but nothing sets them.
- **Reduced motion** collapses `--fb-dur-*` to `0ms` (`@media (prefers-reduced-motion: reduce)`).
- `scripts/ui-check/contrast.mjs` recomputes WCAG contrast for every text/surface pair
  straight from `styles.css` — re-run it after changing any colour token.

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
| `--fb-warning`, `--fb-warning-fg`, `--fb-error-bg`, `--fb-error-fg` | Older badge aliases (`fb-event-level`): alias `warn-fill`/`warn`/`alarm-fill`/`alarm`. |

## Performance

Samples never pass through React state: they land in a `TelemetryStore`
(`packages/react/src/store/`, ring buffers per signal, one websocket per
stream) and charts draw from it directly inside one `requestAnimationFrame`
flush. A chart off screen (`IntersectionObserver`, 200 px margin) or in a
hidden tab does no `setData` and gets one when it returns. `App` holds no
live hook, so a sample re-renders nothing. Counters are on `window.__fb`;
`scripts/ui-check/perf.mjs` measures a page (CDP TaskDuration, long tasks,
heap, render and redraw counts).

## Build

`npm run typecheck` (project references across all three), `npm run build` (packages emit `dist/` with `.d.ts`; the app emits `apps/dashboard/dist`), `npm test` (vitest; `packages/react/test` covers the ring buffer and the store). The `development` export condition resolves packages to source under Vite, so there is no build step in dev.
