# The UI

The dashboard app (`ui/apps/dashboard`) is a pure function of what the server
publishes: schema, telemetry and history over HTTP and websockets, plus
layout it authors itself and the server stores. It knows nothing about any
particular quantity — adding an actuator in Python produces a working page
with no front-end change. This chapter is what the app does; for the visual
language and the reasoning behind it, see `ui/DESIGN-SPEC.md`.

## Pages

| Page | For |
| --- | --- |
| **Dashboards** | saved and generated layouts of widgets — see [Dashboards](dashboards.md) |
| **Overview** | the rig at a glance: a stat tile per channel, a card per reader (its run: period, last read, conditions) and per actuator |
| **Sources** | every source's channels charted, grouped by source or by unit |
| **Controllers** | one card per actuator: with a controller (a loop is always bound to exactly one actuator) the card is the faceplate, its actuator's state/settings/commands open inline below; without one, the actuator card alone plus an "Add controller" button. `#/loops` and `#/actuators` (the two pages this merged) redirect here. |
| **Programs** | the program library (check, run, delete, upload, new) and, for a running or past program, its steps and events |
| **Events** | the rig's event log, live, filterable by level |
| **Sessions** | start/stop recording, list recorded sessions, open one, export, delete |
| **Simulation** | simulation-only controls: clock speed, each plant's live parameters, and per-device faults (`fail`, `restore`, `disturb`, `set_limits`) — these never appear on a controller's actuator section |

## The app bar

A condition summary sits in the app bar, built from `/api/health` (falling
back to a client-side count from the samples stream on an older daemon):

- an always-present **alarm** chip — the count of active device conditions
  plus channels outside their warn/alarm bands, coloured by the worst one
  (amber for warn, red for alarm; otherwise the neutral outline every
  healthy state uses — colour is reserved for abnormal conditions);
- a **stream health** dot (`live` / `reconnecting` / `offline`) folding
  every websocket the page has open;
- a **recording** dot: the open session's name, or "not recording";
- a **program** chip, only while one is running: its name and step;
- a **readers** chip, only while some are not running: `n/total`;
- a **sim** chip, only when the simulated clock is not at ×1: its speed.

Every chip's tooltip lists the names behind the count (conditions, readers)
and links to the page that explains it (Events, Sessions, Programs,
Readers, Simulation).

## The controller faceplate

`LoopPanel` (`ui/packages/react/src/panels/LoopPanel.tsx`) draws one
controller as three aligned rows, each with a bar:

- **Reading** — the process value, with a fill bar against the channel's
  range, warn/alarm band ticks, and a notch at the target; a caption below
  it reading "N °C above/below target".
- **Target** — the setpoint, with the entry/Move control inline (one line,
  wrapping only below 900px) and a caption naming what it is following when
  it is a ramp.
- **Output** — the demand after limits (`loop.expected ?? loop.demand`),
  with a fill bar against the actuator's `output_range`. When the actuator
  cannot give the full demand (`expected` differs from `demand`), the bar's
  fill turns to the alarm tint and the rail it is pinned against gets a 2px
  alarm end-cap — colour on the bar, not a text badge — and a
  **"requested …"** caption names what was asked for.

A banner above the rows reads "reader offline", "actuator at limit" or "open
loop" for the corresponding condition. Process and Drive mini trends (each
with a minimal axis pair: 3-4 y ticks at the channel's precision, sparse time
labels, no legend or toolbar) sit beside the rows; the Drive trend's y-range
is the actuator's `output_range` when known, so "at limit" reads as the line
sitting on the rail. Below the trends, an always-open (no `<details>`) law &
feedforward summary gives the law's tag and every gain on one line, each
abbreviated field carrying its full name as a hover hint, and — on the
Controllers page only, never a dashboard widget — the actuator's own state
and commands, collapsed into the same card so a controller is one card, not
two.

## Stale tiles

A reading, gauge or faceplate whose channel has had no sample for longer
than its reader's period allows (`staleAfterS`) shows the panel frame's
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

**Graph** (`#/graph`) is a free-form chart: pick any readings across any
sources and plot them together, unlike Sources' charts which stay grouped by
source or unit. A picker on the left lists every channel as a tree grouped
by source, with a "by unit" toggle and a search box; a channel shows its
`label || measurand` and unit, with the source (or, grouped by unit, the
channel) as a hover title. Ticked channels draw on one chart that fills the
rest of the page (a narrow screen gets the picker as a drawer instead of a
side panel, opened from the page bar).

The selection is carried in the URL (`#/graph?ch=zone1.temperature,tank.volume`,
so a graph is shareable) and mirrored to `localStorage` (so a plain visit to
`#/graph` comes back to the last one). Each channel keeps the colour slot it
was first ticked into for as long as the page stays open — unticking one
channel never repaints the others, and re-ticking it returns its own colour
(`DESIGN-SPEC.md` §1.2).

Channels of different units share one chart with a y axis per unit rather
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

Known gap: the telemetry store keeps a loop's setpoint/demand/expected ticks
(`TelemetryStore.readLoop`, `useLoopLatest`) but exposes no `TraceRef`-shaped
handle for them the way `useTraceRef` does for channels, so `MultiSeries`
cannot draw a loop overlay by reference; the Graph picker offers channels
only; wiring a loop trace ref through the store is future work.
