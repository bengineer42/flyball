# Charts and the Graph page

**Inputs** (`#/inputs`) charts every publishing signal grouped by device or unit; **Graph** (`#/graph`) plots any signals together. Both use the same chart toolbar, described here with it.

!!! tip "At the terminal"
    `flyball watch samples` is the live stream as JSON lines; `curl` fetches the same exports the toolbar's menu offers -- [Devices and signals](../cli/devices.md), [Sessions and export](../cli/sessions.md).

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

The page bar's window and y-scale controls apply to the Graph chart the same
as everywhere else. There is no "add to dashboard" button; a graph lives
only at its `#/graph` URL.

A full-size chart's y axis draws only the tick nearest its top and bottom
value, not a dense scale; the time axis keeps to two or three sparse,
scrolling labels rather than one per gridline (`panels/yscale.ts#edgeTicks`).
Decimation for a dense trace is automatic, per signal, from how many rows it
actually holds against the chart's width and window — there is no manual
"sample every Nth point" control.

Known gap: the telemetry store keeps a controller's reference/reading/
demand/expected/correction ticks (`TelemetryStore.readController`, its
`ControllerView` shape), but exposes no `TraceRef`-shaped handle for them
the way `useTraceRef` does for signals, so `MultiSeries` cannot draw a
controller overlay by reference; the Graph picker offers signals only;
wiring a controller trace ref through the store is future work.

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
