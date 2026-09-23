# Charts and the Graph page

**Readings** (`#/readings`) charts every published signal grouped by device or unit; **Graph** (`#/graph`) plots any signals together. Both use the same chart toolbar, described here with it.

## Any chart opens almost-fullscreen

Every chart in the app — a dashboard's `chart` and `loop` widgets, a
`readout` widget's or a Readout panel's sparkline, Readings' and Graph's
charts, a controller faceplate's Process/Drive trends, a session's charts —
opens the same way: a full chart (axes, legend, toolbar already showing) by
double-clicking the plot or its toolbar's expand button; a sparkline or a
controller trend (no toolbar of its own at that size) by a single click.
However it opened, it's the same overlay (`ChartOverlay`,
`ui/packages/react/src/panels/ChartOverlay.tsx`): Escape, its Close button,
or a click on the backdrop returns to the page, and focus goes back to
whatever was clicked to open it. A sparkline stays a sparkline in its tile —
opening it doesn't resize the tile, it swaps in the full chart inside the
overlay and swaps back on close. Dragging a dashboard tile in edit mode
doesn't open its chart: react-grid-layout only starts a drag from the tile's
own drag handle, never from a click inside the chart.

A controller faceplate's trends (`ControllerPanel`'s `MiniTrend`, on the
Controllers page and a dashboard's `loop` widget) are a purpose-built,
axes-only uPlot instance while collapsed — no toolbar or legend fits in
~140px — so a click swaps in a real `MultiSeries` chart with `expanded`
forced on, titled for the controller and which trend (`… · process` /
`… · drive`); closing it swaps the mini trend back.

!!! tip "At the terminal"
    `flyball watch samples` is the live stream as JSON lines; `curl` fetches the same exports the toolbar's menu offers -- [Devices and signals](../cli/devices.md), [Sessions and export](../cli/sessions.md).

## Graph

**Graph** (`#/graph`) is a free-form chart: pick any signals across any
devices and plot them together, unlike Readings' charts which stay grouped by
device or unit. A picker on the left lists every numeric published signal
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

Below the device branches, a controller with a regulated signal on this rig
gets a **Setpoints** group of its own (a controller is not a device, so the
unit/device/tag chips above don't filter it, only the search box does):
ticking one plots that controller's setpoint alongside the signals, on the
axis of the signal it regulates (same unit — a controller is named by the
signal it drives), dashed, and labelled `‹signal title› (setpoint)` so it
reads apart from the measured line at a glance. Only the setpoint is
offered here, not the controller's measured/output/expected/correction —
that fuller trace is the Controllers page's `ControllerPanel`, not this
picker.

The selection is carried in the URL (`#/graph?ch=furnace.zone1,level.volume`,
so a graph is shareable) and mirrored to `localStorage` (so a plain visit to
`#/graph` comes back to the last one). A controller's setpoint is carried
the same way, keyed as `controller-setpoint:‹name›` — a shape no signal
address can collide with (an address never contains `:`). Each key keeps
the colour slot it was first ticked into for as long as the page stays
open — unticking one series never repaints the others, and re-ticking it
returns its own colour (the series palette is fixed per slot, not per
signal).

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

The page bar's window and y-scale controls apply to the Graph chart the same
as everywhere else. There is no "add to dashboard" button; a graph lives
only at its `#/graph` URL.

A full-size chart's y axis draws only the tick nearest its top and bottom
value, not a dense scale; the time axis keeps to two or three sparse,
scrolling labels rather than one per gridline (`panels/yscale.ts#edgeTicks`).
Decimation for a dense trace is automatic, per signal, from how many rows it
actually holds against the chart's width and window — there is no manual
"sample every Nth point" control.

The store makes a controller's setpoint readable through the same
`TraceRef` a signal uses: `TelemetryStore.read`/`subscribeTrace` accept a
`controllerSetpointKey(name)` alongside a plain address and read it off the
controller's own ring (the same one `readController`/`ControllerView` use)
rather than a signal's — so `MultiSeries`, fed by `useTraceRef`, draws it
exactly as it draws a signal, live or in playback, with no controller-aware
code of its own. The dashboard's `chart` widget (config-driven, no picker)
does not offer this yet — plotting a controller's setpoint from a rig file
would need a config shape for it (a widget currently only takes signal
addresses) and a small form change, not just the store-level plumbing.

## The window control

The page bar's window control (1 min / 5 min / 15 min / 1 h) sets how much
history a following chart shows; it scrolls once that much has arrived.
Its default is not a fixed constant: the first time both ends of it are
known, it settles to whatever the store has actually loaded, capped at an
hour — a rig with hours of history (`longrun`, say) does not open on a
useless 5 min slice, and one that only just started does not open on an
hour of mostly-empty axis either. It settles once, so it does not keep
widening under a chart the operator hasn't touched as more history streams
in; picking the control by hand always overrides it, and a dashboard
widget's own saved window (`window_s`) never goes through this default at
all.

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
store, **CSV from the store** — the same export the [HTTP API](../../4-server/api.md)
serves, as a plain link. A `LoopPanel`'s trends and a dashboard's charts all
use the same toolbar and the same menu.

## Stale tiles

A reading, gauge or faceplate whose signal has had no sample for longer than
its device's poll period allows (`staleAfterS`) shows the panel frame's
`stale` state: a dashed border, a hollow status dot, no pulse, and a footer
naming how long ago the last sample was — never colour alone (`PanelFrame`,
`ui/packages/react/src/panels/PanelFrame.tsx`).
