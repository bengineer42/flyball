# Dashboards

A dashboard is a saved layout of widgets on a grid: readouts, charts, loop
faceplates, actuator cards, and a few status and layout widgets, each bound
to something on the rig (a channel, a loop, an actuator) or to nothing (a
health strip, a note). It is data, not code — the server stores the document
and validates its outline; the UI owns what a `kind` means and how its
`config` is shaped.

## The generated overview

A rig with no saved dashboards is not blank: `#/dashboards` shows
**"Overview (generated)"**, built from the rig's schema on the fly — a health
strip, a readout per channel, a chart per unit, a faceplate per loop, a card
per actuator. It follows the rig as it changes and is never saved unless you
choose **Save as…**; editing it does not touch anything on disk until then.

## The document

```jsonc
{
  "schema_version": 1,
  "name": "furnace",
  "rig": "furnace",
  "description": "Three zones, a sample thermocouple, and the heaters holding them.",
  "grid": { "cols": 24, "row_height": 24 },
  "widgets": [
    { "id": "w1", "kind": "health", "x": 0, "y": 0, "w": 24, "h": 2, "config": { "tiles": ["rig", "recording", "readers", "loops", "conditions"] } },
    { "id": "w2", "kind": "readout", "x": 0, "y": 2, "w": 6, "h": 5, "config": { "channel": "zone1.temperature", "sparkline": true } },
    { "id": "w3", "kind": "chart", "x": 0, "y": 7, "w": 18, "h": 8, "config": { "channels": ["zone1.temperature", "zone2.temperature"], "window_s": 300, "every": 0, "y": "auto" } },
    { "id": "w4", "kind": "loop", "x": 0, "y": 15, "w": 8, "h": 8, "config": { "loop": "heater1", "view": "compact" } }
  ]
}
```

The grid is 24 columns wide; `x`/`y`/`w`/`h` are grid units, `row_height` is
pixels per row (24 by default). A widget's binding lives inside its own
`config` (there is no separate `bind` field): a `readout`/`gauge` names one
`channel` as `"source.measurand"`, a `chart` names several in `channels`, a
`loop` widget names a `loop`, an `actuator` widget an `actuator`. Every
widget kind's config is documented in the app's own "Add widget" catalogue
(hover the `?` on each field) and in `ui/DESIGN-SPEC.md` §3.

Server side, a dashboard is versioned per name like a program: `PUT
/api/dashboards/{name}` writes a new version, `GET` returns the newest, `GET
.../history` every version. `GET`/`PUT` also return `problems: [{widget_id,
ref, reason}]` — every widget naming a channel, loop or actuator the rig does
not currently have. The document is never refused for this: the widget shows
the "unbound" state instead (a dashed border and the reason) so a renamed
sensor does not cost you the rest of a twenty-widget dashboard. In edit mode,
reconfigure or remove it from the widget's own `⋯` menu.

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
directory next to the rig's `.toml` is imported on daemon start (unchanged
files are skipped; an edited one becomes a new version). See
`examples/simulated/dashboards/{overview,furnace}.json` for the furnace
simulation's own presets — one generic overview, one curated for the
furnace's three zones and heaters.
