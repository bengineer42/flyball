# The UI

The dashboard app (`ui/apps/dashboard`) is a pure function of what the server
publishes: schema, telemetry and history over HTTP and websockets, plus
layout it authors itself and the server stores. It knows nothing about any
particular quantity — adding a device in Python produces a working page
with no front-end change. This chapter is what the app does; for the visual
language and the reasoning behind it, see `ui/DESIGN-SPEC.md`.

## Pages

| Page | For |
| --- | --- |
| **Dashboards** | saved and generated layouts of widgets — see [Dashboards](dashboards.md) |
| **Overview** | the rig at a glance: a stat tile per publishing signal, a card per polled device (its run: period, last read, conditions) |
| **Inputs** | every publishing signal charted, grouped by device or by unit |
| **Controllers** | one card per writable signal: with a controller the card is the faceplate, its device's other signals and commands open inline below; without one, the signal's card alone plus an "Add controller" button. `#/loops` and `#/actuators` redirect here |
| **Programs** | the program library (check, run, delete, upload, new) and, for a running or past program, its steps and events |
| **Events** | the rig's event log, live, filterable by level |
| **Sessions** | start/stop recording, list recorded sessions, open one, export, delete |
| **Simulation** | simulation-only controls: clock speed, each plant's live parameters, and per-device faults (`fail`, `restore`, `disturb`, `set_limits`) — these never appear on a controller's device section |

## The app bar

A condition summary sits in the app bar, built from `/api/health` (falling
back to a client-side count from the samples stream on an older daemon):

- an always-present **alarm** chip — the count of active device conditions
  plus signals outside their warn/alarm bands, coloured by the worst one
  (amber for warn, red for alarm; otherwise the neutral outline every
  healthy state uses — colour is reserved for abnormal conditions);
- a **stream health** dot (`live` / `reconnecting` / `offline`) folding
  every websocket the page has open;
- a **recording** dot: the open session's name, or "not recording";
- a **program** chip, only while one is running: its name and step;
- a **devices** chip, only while some polled devices are not running: `n/total`;
- a **sim** chip, only when the simulated clock is not at ×1: its speed.

Every chip's tooltip lists the names behind the count (conditions, devices)
and links to the page that explains it (Events, Sessions, Programs, Devices,
Simulation).

## The controller faceplate

`LoopPanel` (`ui/packages/react/src/panels/LoopPanel.tsx`) draws one
controller as three aligned rows, each with a bar:

- **Reading** — the process value, with a fill bar against the source
  signal's range, warn/alarm band ticks, and a notch at the target; a
  caption below it reading "N °C above/below target".
- **Target** — the setpoint, with the entry/Move control inline (one line,
  wrapping only below 900px) and a caption naming what it is following when
  it is a ramp.
- **Output** — the demand after limits (a controller's `expected ??
  demand`), with a fill bar against the target signal's `limits`. When the
  device cannot give the full demand (`value` differs from `requested` in
  its write state), the bar's fill turns to the alarm tint and the rail it
  is pinned against gets a 2px alarm end-cap — colour on the bar, not a text
  badge — and a **"requested …"** caption names what was asked for.

A banner above the rows reads "device offline", "signal at limit" or "open
loop" for the corresponding condition. Process and Drive mini trends (each
with a minimal axis pair: 3-4 y ticks at the signal's precision, sparse time
labels, no legend or toolbar) sit beside the rows; the Drive trend's y-range
is the target signal's `limits` when known, so "at limit" reads as the line
sitting on the rail. Below the trends, an always-open (no `<details>`) law &
feedforward summary gives the law's tag and every gain on one line, each
abbreviated field carrying its full name as a hover hint, and — on the
Controllers page only, never a dashboard widget — the device's own state and
commands, collapsed into the same card so a controller is one card, not two.

## Stale tiles

A reading, gauge or faceplate whose signal has had no sample for longer than
its device's poll period allows (`staleAfterS`) shows the panel frame's
`stale` state: a dashed border, a hollow status dot, no pulse, and a footer
naming how long ago the last sample was — never colour alone (`PanelFrame`,
`ui/packages/react/src/panels/PanelFrame.tsx`).

## Density and theme

Two toggles in the app bar, both persisted to `localStorage` and applied as
attributes on `<html>` so the whole app (MUI and the plain-CSS `packages/react`
components alike) reads them from the same CSS custom properties:

- **Theme** (`flyball.theme`): light/dark, defaulting to the OS preference
  (`prefers-color-scheme`) until chosen explicitly (`data-theme`).
- **Density** (`flyball.density`): comfortable/compact (`data-density`),
  changing tile gaps, a tile's title-row height and a readout's minimum
  height.

## Chart keyboard shortcuts

`ChartToolbar` (`ui/packages/react/src/panels/ChartToolbar.tsx`) accepts
these keys while it or its chart has focus, alongside the same buttons:

| Key | Does |
| --- | --- |
| `←` / `→` | pan back / forward half a window |
| `-` / `+` | zoom out / in about the centre |
| `0` | fit everything held on the time axis |
| `l` | return to live (follow the newest data) |

Wheel zooms and drag pans directly on the chart; double-click (or the
toolbar's expand button) opens it full-size.

## Downloads

Every chart's toolbar offers a download menu: **CSV** or **JSON** of what is
currently shown (`onDownload`), plus, where the chart is backed by the
store, **CSV from the store** — the same export the [HTTP API](../6-reference/api.md)
serves, as a plain link. A `LoopPanel`'s trends and a dashboard's charts all
use the same toolbar and the same menu.

## Sessions

Rows carry a checkbox: click ticks one, shift-click ticks the range from the
last-clicked row, and ctrl/⌘-click ticks one without disturbing that anchor.
With one or more ticked, a bar offers **Delete** (through the same confirm
dialog as a single session, naming the count and the ids) and **Clear**; the
open session's row can't be ticked.

## Design rationale

`ui/DESIGN-SPEC.md` is the specification this app implements against: colour
tokens and contrast, the widget catalogue and dashboard editor UX, and the
reasoning behind choices summarised here (why colour is reserved for
abnormal states, why a stale tile is never colour-only, and so on).

## Graph

**Graph** (`#/graph`) is a free-form chart: pick any signals across any
devices and plot them together, unlike Inputs' charts which stay grouped by
device or unit. A picker on the left lists every signal as a tree grouped
by device, with a "by unit" toggle and a search box; a signal shows its
`label || quantity` and unit, with the device (or, grouped by unit, the
signal) as a hover title. Ticked signals draw on one chart that fills the
rest of the page (a narrow screen gets the picker as a drawer instead of a
side panel, opened from the page bar).

The selection is carried in the URL (`#/graph?ch=furnace.zone1,level.volume`,
so a graph is shareable) and mirrored to `localStorage` (so a plain visit to
`#/graph` comes back to the last one). Each signal keeps the colour slot it
was first ticked into for as long as the page stays open — unticking one
signal never repaints the others, and re-ticking it returns its own colour
(`DESIGN-SPEC.md` §1.2).

Signals of different units share one chart with a y axis per unit rather
than the "second unit is a second chart" rule the rest of the app follows
(`DESIGN-SPEC.md` §3.16 is deliberately overridden here, at the user's
request — every other chart in the app still keeps to one unit). `MultiSeries`
(`ui/packages/react/src/panels/MultiSeries.tsx`) draws the extra axes
alternating right/left as more units are added, each axis's ticks coloured
to match the one series on it; past four axes on screen (the chart's own
plus three more) it stops adding individual axes and folds every further
unit onto one shared "more…" axis instead of growing without bound. A
selection of one unit behaves exactly as it did before this page existed —
one axis, no fold.

The page bar's window, sample and y-scale controls apply to the Graph chart
the same as everywhere else. There is no "add to dashboard" button; a graph
lives only at its `#/graph` URL.

Known gap: the telemetry store keeps a controller's reference/reading/
demand/expected/correction ticks (`TelemetryStore.readController`, its
`ControllerView` shape), but exposes no `TraceRef`-shaped handle for them
the way `useTraceRef` does for signals, so `MultiSeries` cannot draw a
controller overlay by reference; the Graph picker offers signals only;
wiring a controller trace ref through the store is future work.
