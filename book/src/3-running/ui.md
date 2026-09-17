# The UI

The dashboard app (`ui/apps/dashboard`) is a pure function of what the server
publishes: schema, telemetry and history over HTTP and websockets, plus
layout it authors itself and the server stores. It knows nothing about any
particular quantity — adding a device in Python produces a working page
with no front-end change. This chapter is what the app does; the visual
language is described under *Design rationale* below and in `ui/README.md`.

## Pages

| Page | For |
| --- | --- |
| **Dashboards** | saved and generated layouts of widgets — see [Dashboards](dashboards.md) |
| **Overview** | the rig at a glance: a stat tile per publishing signal, a card per polled device (its run: period, last read, conditions) |
| **Inputs** | every publishing signal charted, grouped by device or by unit |
| **Devices** | one card per device (signals, commands, conditions); add or remove a device or a link |
| **Controllers** | one card per writable signal: with a controller the card is the faceplate, its device's other signals and commands open inline below; without one, the signal's card alone plus an "Add controller" button. `#/loops` and `#/actuators` redirect here |
| **Programs** | the program library (check, run, delete, upload, new) and, for a running or past program, its steps and events |
| **Events** | the rig's event log, live, filterable by level |
| **Sessions** | start/stop recording, list recorded sessions (and the daemon's rolling record, if it keeps one), keep a range of it, pin, open one, export, delete |
| **Rig** | the running rig as a file would show it, what has changed since the daemon started, its version history (the current one marked), saving it, connecting a model over MCP, and — when the daemon allows — restarting or shutting it down |
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

## Bearer token

A **token** chip sits in the app bar beside the other status chips (a key
icon; click it for a small form with one field). It holds the daemon's
bearer token — see [the daemon's "The token"](daemon.md#the-token) for what
that gets a client and what it does not — kept in this browser's
`localStorage` (`flyball.token`) so it survives a reload, and taken once
from `?token=…` on the page's own URL if it is there (then dropped from the
visible address, so it is not left in history or in a copied link). The
client puts it on every `/api` request as `Authorization: Bearer …` and on
every `/ws` URL as `?token=…`, the one place a browser cannot set a header;
changing it rebuilds the client and reconnects every socket at once.

Without a token, or the wrong one, the daemon's first refusal — `GET
/api/devices`, the very first thing the app asks for — replaces the whole
page with "This rig needs a token" and the same field, front and centre
rather than left for a person to go hunting for the small chip.

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

On a daemon that keeps a rolling record while nothing is being recorded
(`keep:` in its `daemon:` section — see [the daemon](daemon.md)), the list
also shows that record as one row: "last 58 min held — not a recording",
with what the daemon trims it to and how much it holds. It is never a
session until you make one of it: **Keep…** takes the last 5, 15, 30 …
minutes (as much as is held) plus a name and notes and produces a closed
session like any other; and **Start recording** gains an **include the
last …** choice, so a recording started after something happened still
contains it. Charts seed their history from the rolling record exactly as
from a session, so an unrecorded rig shows its last hour on page load.

Where the daemon retains (`retain:`), each closed session's "ended" cell
says when it will be aged out, and a **pin** on the row keeps it past that;
a session the daemon continued at a rotation boundary (`rotate:`) carries a
`continues #n` chip back to the one before it. The section head states the
daemon's policy in one line — kept, retained, rotated, capped, and where
the store lives.

## Devices

**Devices** (`#/devices`, or `#/devices/<name>` for one alone) is a card
per device: signals grouped by namespace, with a toggle to pivot by `tags`
section where the device has one; commands as cards; `conditions`, `mode`
and `last.*` drawn as the list, chip and "ran at" lines they are rather
than raw JSON. In the side menu the **Devices** entry opens (a chevron,
open by itself while a device page is showing) into one link per device,
so a device is one click from anywhere.

A command card is one type scale — title, then labels, then controls — and
cards in a row share a height with **Run** on the bottom line. A field's
description is not printed under it: an ⓘ beside the label carries it on
hover (and for a screen reader). A choice of two kinds is two equal halves,
of three or more a stacked list.

- **Add link** builds a link — a bus, a simulated plant, anything a rig
  file's `links:` takes — from the rig's schema (`GET /api/rig/schema`): a
  kind picker, then a `SchemaForm` for its config.
- **Add device** builds a device on the rig the same way: a name, a driver
  picker, that driver's config as a `SchemaForm` (a `link` field the schema
  names becomes a select of the rig's current links once there are any,
  else a free-form box), a label, a poll period, and any bound inputs
  (`role -> address`) the driver takes.
- Removing a device or a link takes everything built on it down too, after
  a confirmation naming what that is; a link still carrying a device
  refuses (409) until the device is removed first.

## Rig

**Rig** (`#/rig`) is the running rig as a file would show it, alongside its
history and how to reach it from outside the browser:

- **Running document** (`GET /api/rig/document`) — links, devices and
  controllers as they are now, as read-only YAML.
- **Changed since start** (`GET /api/rig/changes`) — an overlay of what
  differs from the files the daemon loaded (a key removed appears as
  `null`), highlighted once it is non-empty.
- **Versions** (`GET /api/rig/versions`) — every version the store has
  seen, newest first, the one the running rig is at marked **current**
  (its restore is disabled) and each row saying which version it was made
  from; **restore** (`POST /api/rig/versions/{id}/restore`) rebuilds the
  running rig to match that version and moves the head there.
- **Save** (`POST /api/rig/save`) — with no path, just what changed since
  start, written to an overlay beside the file the rig was loaded from; a
  path writes the whole rig there instead, with a checkbox to overwrite a
  loaded file. The path field only appears on a daemon that allows it
  (`allow_save`); otherwise the box says so and saves the overlay alone.
- The section head shows where the daemon serves from and how many files
  it loaded; on a daemon started with `allow_shutdown`, **Restart** and
  **Shut down** buttons beside it, each behind a confirmation. Restart
  runs the same command again: the rig is rebuilt from its files, the app
  reconnects within a few seconds.
- **Connect a model** — this daemon's [MCP](mcp.md) server, one tier per
  mode: each row is that tier's absolute URL, a ready-made
  `claude mcp add --transport http …` line, and (below all three) a client
  config block naming all of them, one copy button each. The config's
  `headers` carry the app's own bearer token (above) once it has one, and a
  note in its place when it does not.

## Design rationale

Colour is reserved for abnormal states (ISA-101): a normal reading is
neutral, warn and alarm change the tile's border and never only its colour,
and a stale tile is dashed with a hollow status dot. Every colour, space,
radius and duration is a token in `ui/packages/react/src/styles.css`
(`ui/README.md` *Theming* lists them with their purpose and contrast).

## Graph

**Graph** (`#/graph`) is a free-form chart: pick any signals across any
devices and plot them together, unlike Inputs' charts which stay grouped by
device or unit. A picker on the left lists every numeric publishing signal
under its device, each by its title (a signal the driver left unlabelled, or
whose label another signal of the device shares, is named by its namespace
too: `Dry line humidity`, never three `Humidity`) with its unit; above the
list a search box and a row of filter chips per axis — **unit**, **device**,
and every tag axis the rig's signals carry (`Line: Chamber / Dry / Wet` on
the humidity rig). Chips combine across rows; "clear filters" resets them.
Between the filters and the list, **select all** and **none** act on the
signals currently shown, so "everything in %RH on the dry line" is two
chips and a click. Ticked signals draw on one chart that fills the rest of
the page (a narrow screen gets the picker as a drawer instead of a side
panel, opened from the page bar).

The selection is carried in the URL (`#/graph?ch=furnace.zone1,level.volume`,
so a graph is shareable) and mirrored to `localStorage` (so a plain visit to
`#/graph` comes back to the last one). Each signal keeps the colour slot it
was first ticked into for as long as the page stays open — unticking one
signal never repaints the others, and re-ticking it returns its own colour
(the series palette is fixed per slot, not per signal).

Signals of different units share one chart with a y axis per unit rather
than the "second unit is a second chart" rule the rest of the app follows
(deliberately, at the user's request — every other chart in the app
still keeps to one unit). `MultiSeries`
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
