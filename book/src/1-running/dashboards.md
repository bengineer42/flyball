# Dashboards

A dashboard is a saved layout of widgets on a grid: readouts, charts,
controller faceplates, device cards, and a few status and layout widgets,
each bound to something on the rig (a signal's address, a controller, a
device) or to nothing (a health strip, a note). It is data, not code — the
server stores the document and validates its outline; the UI owns what a
`kind` means and how its `config` is shaped.

!!! tip "At the terminal"
    None: dashboards are a browser thing. Their documents are routes (`/api/dashboards`, [Dashboards](../4-server/api.md#dashboards)) and files under `dashboards/` beside the rig.

## The generated overview

A rig with no saved dashboards is not blank: `#/dashboards` shows
**"Overview (generated)"**, built from the rig's schema on the fly — a health
strip, a readout per published signal, a chart per unit, a faceplate per
controller, a card per device. It follows the rig as it changes and is never
saved unless you choose **Save as…**; editing it does not touch anything on
disk until then.

## The document

```jsonc
{
  "schema_version": 3,
  "name": "furnace",
  "rig": "furnace",
  "description": "Three zones, a sample thermocouple, and the heaters holding them.",
  "grid": { "cols": 24, "row_height": 24 },
  "readonly": false,
  "order": null,
  "widgets": [
    { "id": "w1", "kind": "health", "x": 0, "y": 0, "w": 24, "h": 2, "config": { "tiles": ["rig", "recording", "devices", "controllers", "conditions"] } },
    { "id": "w2", "kind": "readout", "x": 0, "y": 2, "w": 6, "h": 5, "config": { "address": "furnace.zone1", "sparkline": true } },
    { "id": "w3", "kind": "chart", "x": 0, "y": 7, "w": 18, "h": 8, "config": { "addresses": ["furnace.zone1", "furnace.zone2"], "window_s": 300, "every": 0, "y": "auto" } },
    { "id": "w4", "kind": "loop", "x": 0, "y": 15, "w": 8, "h": 8, "config": { "controller": "heaters.heater1", "view": "compact" } }
  ]
}
```

The grid is 24 columns wide; `x`/`y`/`w`/`h` are grid units, `row_height` is
pixels per row (24 by default). A widget's binding lives inside its own
`config` (there is no separate `bind` field): a `readout`/`gauge` names one
signal as `address`, a `chart` names several in `addresses`, a `loop`
widget names a `controller`, a `device` widget a `device`. Every
widget kind's config is documented in the app's own "Add widget" catalogue
(hover the `?` on each field) and as data at
`GET /api/dashboards/widgets`: every kind with its label, category, sizes
and config schema, the rig-dependent fields marked `x-binding: signal |
controller | device` — what a client writing a document by hand, or a
model doing it over MCP, reads. That catalogue is generated from the
widget registry (`npm run widgets-json` in `ui/`); run it after changing
a widget.

Server side, a dashboard is versioned per name like a program: `PUT
/api/dashboards/{name}` writes a new version, `GET` returns the newest, `GET
.../history` every version. `GET`/`PUT` also return `problems: [{widget_id,
ref, reason}]` — every widget naming an address, controller or device the
rig does not currently have. The document is never refused for this: the
widget shows the "unbound" state instead (a dashed border and the reason) so
a renamed sensor does not cost you the rest of a twenty-widget dashboard. In
edit mode, reconfigure or remove it from the widget's own `⋯` menu.

`readonly` marks a dashboard for looking at, not operating: every write
control on it, from a device widget's commands to the recording widget's
Start and End and the program widget's Cancel, shows but is disabled.
Toggle it from the page bar's `⋯` menu (**Make read-only** / **Make
writable**); like any edit, it takes effect at once and **Save** keeps it.
It is a convenience for a wall display, not access control: anyone who may
save the dashboard may turn it off. A screen that must not operate the rig
should be a browser without the `operate` verb (see
[access](runner/access.md)). Layout editing is unaffected. `order` places
the dashboard among the others, ascending; dashboards without one follow,
newest saved first. A version-2 document has neither and reads as
writable and unordered.

A document saved before the device model is `schema_version: 1` (bindings
to channels, loops and actuators); it is migrated on read, never refused,
and what is stored on disk stays exactly as saved — `channel`
(`"source.measurand"` or `{source, measurand}`) becomes `address`,
`channels` becomes `addresses`, a `loop` widget's `loop` becomes
`controller`, and an `actuator` widget becomes a `device` widget bound by
`device`. A loop was named by its actuator and a controller by its target's
address, so a migrated `loop` binding may show as a problem until it is
rebound.

## Editing

**Edit** puts the page bar into edit mode: drag a tile by its title strip,
resize from its corner, **+ Add widget** to add another (the catalogue is
grouped by kind, with search and a cost tag — cheap/chart/heavy), and each
tile's `⋯` menu to configure, duplicate or remove it. Undo/redo (`Ctrl+Z` /
`Ctrl+Shift+Z` or `Ctrl+Y`, up to 20 steps) covers every edit. **Done** with unsaved
changes offers Save, Discard or Keep editing.

The grid itself is plain CSS in view mode — nothing is mounted to make it
draggable — and only becomes a drag-and-resize grid (`react-grid-layout`)
once you click Edit; that is the app's largest single saving in an otherwise
idle view.

## Saving, naming, switching

The switcher in the app bar lists this rig's dashboards, default first, then
alphabetically, alongside the generated overview. `Save ▾` offers:

- **Save** — a new version under the current name.
- **Save as…** — under a new name; the generated overview must go through
  this once before it can be saved at all.
- **Rename…**, **Delete…** (with confirmation).
- **Set as home** — `#/` opens this dashboard instead of the Overview page,
  remembered per browser (`localStorage`), not written to the document.
- **Export JSON** / **Import JSON…** — the document above, pretty-printed.
  Import loads the file as a draft under its own name; **Save** commits it,
  the same as any other edit.

## Presets

A rig can ship dashboards beside its file: anything in a `dashboards/`
directory next to the rig's file is imported on runner start (unchanged
files are skipped; an edited one becomes a new version). See
`examples/simulated/dashboards/{overview,furnace}.json` for the furnace
simulation's own presets — one generic overview, one curated for the
furnace's three zones and heaters. Those two files still carry
`schema_version: 1` on disk; the runner serves them migrated, exactly as it
would any other version-1 dashboard.
