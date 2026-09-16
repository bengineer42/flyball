# flyball dashboard: layout / UX research and recommendations

Scope: `ui/apps/dashboard/src` (Shell.tsx, app.css, theme.tsx, pages/Overview.tsx, YScaleSelect.tsx, cards.tsx) and `ui/packages/react/src` (styles.css, panels/Readout, UnitCharts, MultiSeries, TimeSeries, LoopPanel). Nothing modified.

## 0. Root causes found in the code (the symptoms the user sees)

| Symptom | Cause | File |
|---|---|---|
| Page capped ~1420 px | `<Box component="main" sx={{ ..., p: 2.5, maxWidth: 1500 }}>` (spacing unit is 6, so 15 px padding each side → ~1470 px content) | `Shell.tsx` |
| Each source is one narrow card in a column, 70 % empty | Every source gets its **own** `.tiles` grid (`repeat(auto-fill, minmax(13rem, 1fr))`). `auto-fill` keeps the empty tracks, so a 1-channel source renders one 13-rem card in column 1 and 8 empty columns. | `Overview.tsx` (`.source-group > .tiles`), `app.css` |
| "°C" chart draws in the left third of its card | `UnitCharts` returns one `<section>` per unit and Overview wraps them in `.tiles.tiles-wide` (`minmax(26rem, 1fr)`). At ~1470 px that is 3 tracks; 2 units occupy 2 of them, each chart is 1/3 wide. uPlot itself sizes to `el.clientWidth` and has a ResizeObserver, so the chart is correct — the grid track is wrong. | `Overview.tsx` line ~`<div className="tiles tiles-wide"><UnitCharts …/>` |
| Nothing lines up | Three spacing systems: MUI `spacing: 6` (6/9/12/15 px), library `--fb-gap: 0.75rem` (12 px), ad-hoc `0.9rem`, `1.5rem`, `0.6rem 0.75rem`. Section head is a 0.8 rem uppercase `h2` next to 40 px-tall outlined MUI `Select`s with floating labels. | `theme.tsx`, `app.css`, `styles.css`, `YScaleSelect.tsx` |
| Readouts cramped | `.fb-readout-value` is `1.7em` of a 13 px base ≈ 22 px, fixed regardless of tile width; sparkline fixed 44 px. | `styles.css`, `Readout.tsx` |
| Colour everywhere | Every actuator card shows a green `ok` chip; readers show green `running`; stats strip colours `ok` green. Normal state is not visually quiet. | `Overview.tsx`, `cards.tsx` |

## 1. Prior art surveyed (what each does that is relevant)

- **Grafana** — 24-column grid, height in 30 px units (`gridPos`), "negative gravity" (panels float up); new *Auto grid* layout: min column width (Standard/Narrow/Wide/Custom px), max columns (≤10), row height (Standard/Short/Tall/Custom), *Fill screen* toggle. Stat panel: orientation auto, text size auto, colour mode none/value/background, sparkline in background, thresholds. Best practice: "Dashboards should reduce cognitive load", rows reflect hierarchy, general → specific.
  https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/create-dashboard/ · https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/stat/ · https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/best-practices/ · https://grafana.com/docs/grafana/latest/as-code/observability-as-code/schema-v2/layout-schema/
- **Home Assistant sections view** — rejected masonry ("compatibility with multiple screen sizes and easy drag-and-drop cannot co-exist"; a 1 px height difference reshuffles columns; muscle memory lost). Chose the *Z-grid*: sections flow left→right, wrap; cards have regular row height and column-width multiples; Tile/Button/Sensor cards are grid-sized, other cards default to full section width; per-card min/max size; "dense" packing is opt-in and only fills horizontal gaps.
  https://www.home-assistant.io/blog/2024/03/04/dashboard-chapter-1/ · https://www.home-assistant.io/dashboards/sections/ · https://www.home-assistant.io/dashboards/cards/ · https://www.home-assistant.io/blog/2024/07/03/release-20247/
- **Ignition Perspective** — Flex container = CSS flexbox with per-child grow/shrink/basis; Column container = 12-column grid with three breakpoints (sm/md/lg by `minWidth`), per-breakpoint `span/rowIndex/colIndex/order`, px gutters; Breakpoint container swaps content entirely.
  https://www.docs.inductiveautomation.com/docs/8.1/appendix/components/perspective-components/perspective-container-palette/perspective-flex-container · https://www.docs.inductiveautomation.com/docs/8.3/appendix/components/perspective-components/perspective-container-palette/perspective-column-container
- **Node-RED Dashboard 2.0** — 12-column CSS grid; groups span N columns and expose an internal N-column grid; row height = tallest widget in the row; Fixed layout uses 90 px units and 48 px rows; breakpoints change the default column count.
  https://dashboard.flowfuse.com/layouts/types/grid.html
- **EPICS Phoebus / CS-Studio Display Builder** — alarm-sensitive borders drawn *around* the widget without changing its size; severity encoded by colour **and line type** so colour-blind users can tell them apart; the disconnected border can never be disabled; severities OK < MINOR < MAJOR < INVALID < UNDEFINED, with `_ACK` variants; the alarm table "ideally is empty".
  https://github.com/ControlSystemStudio/phoebus/blob/master/app/display/Readme.md · https://control-system-studio.readthedocs.io/en/latest/app/alarm/ui/doc/
- **ISA-101 / High-Performance HMI** — ~90 % of the screen neutral grey; colour reserved for abnormal ("Look here now"); analog indicator with a normal-band shown next to the value; embedded sparklines ("60–120 px wide, 15–30 px tall") beside live values; deviation indicator with setpoint centred, pointer grey inside band / amber near edge / red beyond; display levels 1 (overview KPI tiles) → 2 (area) → 3 (loop faceplate: SP/PV/OP, deviation, trend) → 4 (tuning/diagnostics). Reported 48 % improvement in detecting abnormal situations before alarms.
  https://industrialmonitordirect.com/blogs/knowledgebase/isa-101-high-performance-hmi-design-principles-color-strategy · https://www.realpars.com/blog/high-performance-hmi
- **LabVIEW** — panes separated by splitter bars; "Scale all Objects with Pane" makes controls resize live with the pane.
  https://www.ni.com/docs/en-US/bundle/labview/page/scaling-front-panel-objects.html
- **Foxglove** — mosaic of split panes + Tab panels ("Group in tab"); layouts saved per person/org and shareable. Relevant only as a possible "workspace" mode; not recommended for the default.
  https://docs.foxglove.dev/docs/visualization/layouts
- **MUI density** — density via theme `defaultProps` (`size: 'small'`, `margin: 'dense'`) on 13 components; warning that theme-wide high density "might negatively impact user experience".
  https://mui.com/material-ui/customization/density/
- **CSS container queries** — `container-type: inline-size`, `@container (width > …)`, `cqi` units. https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_containment/Container_queries
- Not fetched / [Unverified]: Bluesky queue-server UIs, Chronograf/Kibana, Perfetto — no primary documentation read; nothing below relies on them.

## 2. Recommendations (priority order)

### P1 — Remove the width cap; let the main area be a 12-column grid
Prior art: Grafana 24 col, Ignition/Node-RED 12 col; all fill the viewport. HA fills the viewport with a capped *number of sections*, not a px cap.
Change `Shell.tsx`: drop `maxWidth: 1500`; keep `minWidth: 0`. Then in `app.css`:
```css
.page { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: var(--gap); align-content: start; }
.span-12 { grid-column: span 12; } .span-8 { grid-column: span 8; } .span-6 { grid-column: span 6; } .span-4 { grid-column: span 4; } .span-3 { grid-column: span 3; }
@media (max-width: 1200px) { .span-8, .span-6, .span-4, .span-3 { grid-column: span 12; } }
@media (min-width: 1201px) and (max-width: 1800px) { .span-4, .span-3 { grid-column: span 6; } }
```
Overview at 2560 px then becomes: stats strip `span-12`; unit charts `span-8`; loops faceplate column `span-4`; actuators `span-6`; readers `span-6`. Trade-off: two breakpoints of hand-tuned spans versus HA's simpler Z-grid; the spans are a handful of class names, no layout persistence needed. If you want an absolute ceiling for readability, use `max-width: 2400px; margin-inline: auto` — not 1500.

### P1 — One shared readout grid per page, not one per source
HA: tile cards are grid-sized units in one section grid; a "heading card" occupies a full row. Grafana: repeated panels flow horizontally with `Max per row`.
Make the source name a full-width heading row *inside* the same grid instead of nesting a grid per source:
```css
.readouts { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 12rem), 1fr)); grid-auto-rows: 8.5rem; gap: var(--gap); }
.readouts .source-head { grid-column: 1 / -1; align-self: end; min-height: 1.75rem; }
```
In `Overview.tsx` render `sources.flatMap(src => [<SourceHead/>, ...src.channels.map(Readout)])` into one `.readouts`. Alternative when you want tiles of one source to stay together on a row: keep per-source groups but switch them to `auto-fit` *and* cap tile growth: `grid-template-columns: repeat(auto-fit, minmax(12rem, 18rem))` — tiles then fill leftwards without becoming 900 px wide. Trade-off: `auto-fit` with a max leaves a ragged right edge; the shared grid is neater.

### P1 — Charts fill their track; chart height follows width
The chart card's inner `.tiles.tiles-wide` must go. Use a dedicated grid for unit charts:
```css
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 36rem), 1fr)); gap: var(--gap); }
```
Two units → 50/50; one unit → full width; four → two rows. In `MultiSeries`/`TimeSeries` the ResizeObserver already calls `setSize({ width: el.clientWidth, height })`; add a `height="auto"` mode: `height = clamp(160, Math.round(width * 0.3), 360)` computed in the observer (Grafana *Fill screen* / row-height analogue). Keep the explicit `height` prop for sparklines. Note: `MultiSeries` rebuilds on `[shape, unit, height, …]`, so compute height inside the observer and call `setSize`, do not put it in React state, or every resize destroys the chart.

### P1 — One spacing scale
Node-RED 48 px rows, Grafana 30 px, HA regular row multiples: all integer grids. Set `spacing: 4` in `makeTheme` (4/8/12/16/24 via `p: 1..6`), define `--gap: 12px` (or `theme.spacing(3)`) once in `FbVars` and set `--fb-gap` from it, and replace the `0.9rem/0.75rem/1.5rem` literals in `app.css` and `styles.css` with `var(--gap)` / `calc(var(--gap) * 2)`. Card padding `p: 3` (12 px) everywhere, section gap 24 px. Trade-off: touching `spacing` rescales every `sx` in the app; do it once and re-check Programs/Sessions tables.

### P2 — Sticky page toolbar; chart controls live once, top-right
Grafana's time picker is global, top-right, never per panel. Move `ChartControls` out of every `SectionHead`/page and into a sticky bar under the AppBar:
```tsx
<Box sx={{ position: "sticky", top: 48, zIndex: 1, bgcolor: "background.default", borderBottom: 1, borderColor: "divider", py: 1, display: "flex", gap: 2, alignItems: "center" }}>
  <Typography variant="h1">{title}</Typography><Box flexGrow={1}/><ChartControls …/>
</Box>
```
Replace the three floating-label `Select`s with `ToggleButtonGroup size="small"` segments (window `1m 5m 15m 1h`, sample `1 2 5 10`, y `auto|range|custom`) at 28–30 px height so they share a baseline with the h1. Keep `YScaleSelect`'s min/max fields but show them only when `custom` is chosen. Section heads then hold only title + one "→ charts" link, `min-height: 32px`, `align-items: center`.

### P2 — Readout tiles: fixed row height, value scales with tile width
Grafana stat: "text size auto", value + name, sparkline behind; ISA-101: value + normal-band indicator + sparkline inline. In `styles.css`:
```css
.fb-readout { container-type: inline-size; display: flex; flex-direction: column; height: 100%; }
.fb-readout-value { font-size: clamp(1.5rem, 11cqi, 2.75rem); line-height: 1.1; font-weight: 500; }
.fb-readout .fb-chart { flex: 1 1 auto; min-height: 32px; }
```
With `grid-auto-rows: 8.5rem` on the grid every tile is the same height and the sparkline takes the remainder; pass sparkline height from a ResizeObserver or set `height` via `cqb`. Keep `tabular-nums` and the reserved `minWidth: ${width}ch` — that is already right. Add the dense/comfortable toggle (below) to switch `grid-auto-rows` 7rem/9rem.

### P2 — Colour semantics: quiet by default (ISA-101), border + line type for alarms (Phoebus)
Currently every healthy card carries a coloured chip. Change: `ok`/`running` chips → `variant="outlined" color="default"` (grey text, no fill); colour only for `warn` (amber), `alarm`/`fault` (red), and *stale/disconnected* (grey dashed border, always shown). Encode severity in the tile border, not the tile body:
```css
.fb-readout.fb-alarm-warn  { border: 2px solid var(--fb-warn); }
.fb-readout.fb-alarm-alarm { border: 2px solid var(--fb-alarm); }
.fb-readout.fb-stale       { border: 2px dashed var(--fb-muted); }
```
Map to MUI palette: `warning.main` amber, `error.main` red, `success.main` reserved for *transitions* (recording started, program completed toasts), not steady states. The stats strip should be grey unless a tile is abnormal, so `tone="ok"` should not colour the icon. Trade-off: users accustomed to "green = good" lose a reassurance cue; ISA-101 evidence says detection of abnormal states improves. Series colours in `--fb-series-*` are fine (charts are the exception); check them against the dark palette — `#2557a7` on `#181d24` is low-contrast, use the theme's `primary.main` (`#7aa7e6`) as series-1 in dark mode.

### P2 — Loops as faceplates (Level-3 display)
Process-control faceplate convention: mode badge on top; PV (reading), SP (setpoint), OP (demand/output) as three aligned numeric rows with a bar each — PV bar with normal band and SP notch, OP bar 0–100 % (or the demand unit's range); deviation PV−SP shown once; auto/manual toggle and SP entry on the faceplate. Current `LoopPanel` puts the six readouts *after* two charts. Layout:
```css
.fb-loop { display: grid; grid-template-columns: minmax(15rem, 19rem) 1fr; gap: var(--gap); }
.fb-loop-faceplate { display: grid; grid-template-rows: auto auto auto auto 1fr; }
```
Left column: mode badge (`manual` grey, `open` amber outline, `regulating` primary), then `PV`, `SP`, `OP` rows (`dt` label 0.8em muted, `dd` 1.4em tabular), each with a `.fb-range` bar (already exists for Readout — reuse, adding an SP tick), then `deviation` and the law tag; controls (`LoopControls`) below. Right column: the *Process* and *Drive* charts stacked. Keep the plain-English labels the panel already uses (reading/setpoint/demand/achievable) but add the SP/PV/OP initials as the `dt` prefix so operators from a DCS background read it at a glance. Trade-off: on <1200 px collapse to one column (faceplate above charts).

### P3 — Density toggle (comfortable / compact)
MUI: density via theme defaults; Material warns against global dense. Implement as a second stored preference beside `flyball.theme`: `makeTheme(mode, density)` with `spacing: density === "compact" ? 4 : 6`? No — keep `spacing` fixed (see P1) and vary only: `--gap` (8 vs 12 px), `grid-auto-rows` (7 vs 9 rem), chart `height` clamp (140–280 vs 180–360), `MuiTable` size. Expose in the AppBar next to the theme toggle. Persist like `readStored/writeStored`.

### P3 — Section header component, used everywhere
Grafana rows / HA "heading card": title left, actions right, consistent height. Move `SectionHead` from `Overview.tsx` into `cards.tsx` and use it in Sources, Loops, Actuators, Readers, Sessions, Programs; props `{ icon, title, count?, action?, end? }`, `sx={{ minHeight: 32, mb: 1 }}`. Show the count (`Sources · 4`, `Loops · 2`) as muted text; put the "charts →" link as a `Button size="small" variant="text"` in `end`.

### P3 — Empty states
Current: a lone grey sentence. Use one `EmptyState` (`Paper` outlined, dashed border, centred icon + one sentence + optional action) at `grid-column: 1 / -1`; e.g. Loops: "No loops attached. [Add loop]"; Sources: "No sources declared — declare readers in the rig file" with a link to the docs page. Sim page: same for "not simulated".

### P3 — Overview information hierarchy
Grafana: general → specific, rows reflect flow; ISA-101 Level 1 = KPI tiles + high-level alarms. Order for Overview: stats strip → active conditions (already) → **loops faceplates compact** (SP/PV/OP only, no charts) → readouts → unit charts → actuators/readers. Loops are the thing an operator watches; today they are not on the Overview at all.

### P4 — Optional: user-arrangeable panels
Foxglove/Grafana/HA all let users resize or reorder. Not recommended now: it needs layout persistence, a drag library (`react-grid-layout`) and conflicts with the auto-fill grids above. Revisit only if users ask for per-rig layouts; if so, store `{panel: span}` in localStorage keyed by rig name and apply as `grid-column: span N` — the 12-column grid from P1 makes that a small step.

## 3. User-composed, saveable dashboards

What the prior art does:
- **Grafana** — a dashboard is one JSON object (`uid`, `title`, `panels[]` each with `gridPos {x,y,w,h}` on the 24-col grid and a typed `options`, `templating` variables, `time`, `version` incremented on every save, `schemaVersion` for migrations); edited in a UI edit mode with drag/resize, or via the JSON Model tab; *library panels* are panels stored once and referenced from many dashboards, edits propagate to all instances.
  https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/view-dashboard-json-model/ · https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/manage-library-panels/
- **Home Assistant** — default *storage mode* (UI-edited, server-held JSON) or *YAML mode* (files); a new dashboard is auto-generated from entities until the user "takes control", after which it never auto-updates again; many dashboards per instance, per-user/per-purpose; edit mode with add/move/resize, undo/redo and a raw config editor.
  https://www.home-assistant.io/dashboards/dashboards/ · https://www.home-assistant.io/dashboards/sections/
- **Ignition Perspective** — views are designed in a desktop Designer, not by operators at runtime; layout is per container type (flex/column/coordinate/breakpoint) with per-breakpoint positions stored in the view. [Inference] operators do not compose dashboards; engineers do.
  https://www.docs.inductiveautomation.com/docs/8.3/appendix/components/perspective-components/perspective-container-palette/perspective-column-container
- **Foxglove** — a layout is a tree: `SplitContainer{direction: row|column, items[{proportion, content}]}`, `TabContainer{tabs[{title, content}]}`, leaf `Panel{type, config}`; saved server-side as *personal* or *organization* layouts, "Share with team", explicit Save/Revert of local changes; also composable from Python code.
  https://docs.foxglove.dev/docs/visualization/layouts · https://foxglove.dev/blog/announcing-programmatic-layouts-in-foxglove
- **Phoebus / CS-Studio** — displays are `.bob` XML files edited in the Display Builder editor (snap-to-grid, align, distribute); *macros* parameterise one display for many devices (`$(PV)`), embedded displays compose them; files live in version control, not a database. Runtime users navigate, they do not edit.
  https://github.com/ControlSystemStudio/phoebus/blob/master/app/display/Readme.md · https://control-system-studio.readthedocs.io/en/latest/app/display/editor/doc/index.html

What fits flyball (UI.md §4 already specifies most of this; the codebase has the pieces):
- **Model**: keep UI.md's `Layout{name, panels[]}` with `Panel{kind, title, grid{x,y,w,h}}` discriminated on `kind` (`graph`, `readout`, `gauge`, `actuator`, `loop`, `events`, `stats`). It is the Grafana shape on a 12-column grid, and every `Channel`/actuator/loop reference validates against the rig registry so a stale layout fails loudly, not blank (Phoebus macros solve the "same panel, different device" problem — a `LoopPanel{loop: "$(loop)"}` template is a later step).
- **Storage**: a `layout` table beside `tuning` in `db/sqlite.py` (`name, owner, json, version, created_ns`), append-only like tunings so `GET /api/layouts/{name}` returns the newest and history is free; routes `GET/PUT/DELETE /api/layouts[/{name}]` in a new `server/routes/layouts.py`. Server, not browser storage (UI.md rule); browser keeps only "last opened layout". Grafana's `version` counter → use the row id; add `schema_version` for migrations from day one.
- **Presets per rig**: HA's "auto-generated until you take control" is the right default: the server generates `default` from the schema (one readout grid of every source, one unit-chart, one faceplate per loop, one actuator card each) and regenerates it while it is untouched; saving under a new name freezes it. Bundle rig-specific presets (e.g. `humidity`) as JSON next to the rig file, loaded on first run — same mechanism `routes/library.py` uses to import program files from a directory.
- **Interaction**: a single *Edit layout* toggle in the sticky toolbar (Grafana/HA edit mode). In edit mode panels get a drag handle and resize corner via `react-grid-layout` (`cols=12`, `rowHeight=30`-ish, `compactType="vertical"` = Grafana's negative gravity), an *Add panel* menu that opens the existing `SchemaForm` pointed at the `Panel` JSON Schema (UI.md: "the form component pointed at the layout schema"), *Save / Save as / Revert* (Foxglove). Outside edit mode the grid is static and the auto-fill recommendations in §2 apply *inside* panels (a readout panel is itself an auto-fill grid of tiles).
- **Sharing**: layouts are named rows, so a URL `#layout=<name>` is the share link; export/import as JSON via the raw editor (Grafana JSON Model / HA raw config) covers copying between rigs. Defer per-operator ownership until there is authentication; store `owner` now, ignore it.
- Trade-offs: `react-grid-layout` fixes panel heights in row units, so charts must size to their panel (P1 "chart height follows width" becomes "follows panel h"); auto-fill grids inside panels and a drag grid outside are two systems — acceptable because the boundary (panel) is explicit. The fixed sections layout in §2 remains the fallback when no layout is saved.

## 4. What LabVIEW front panels have that this UI does not

Sources: waveform chart vs graph and the default 1024-point history from https://knowledge.ni.com/KnowledgeArticleDetails?id=kA03q000000YI6UCAW&l=en-US and http://physics.wku.edu/phys318/faq/labview-fundamentals/charts-vs-graphs/; pane scaling from https://www.ni.com/docs/en-US/bundle/labview/page/scaling-front-panel-objects.html. The NI controls-palette pages returned only navigation chrome, so the palette list below is from general knowledge [Unverified].

| LabVIEW feature | flyball today | Borrow? |
|---|---|---|
| Numeric indicator vocabulary: gauge/meter (dial), tank, thermometer, slide, with visible scale and coloured ramp zones | `Gauge.tsx` already has `thermometer|tank|dial|bar` chosen by unit, with warn/alarm zones; used only on ChannelDetail | **Yes** — expose `kind` in the readout/gauge panel config and let a layout choose it; add the scale ticks to `bar` so the readout's range bar reads as an instrument, per ISA-101 normal-band indicator |
| Numeric controls: knob, dial, slide, numeric with increment arrows, per-control range/increment/display format | `form/widgets.tsx` has stepper, slider, segmented, toggle | **Partly** — slider + numeric with unit is enough; skip knobs (poor mouse ergonomics, no keyboard story) |
| Boolean indicators/controls: LED, round/square LED, rocker/toggle/slide switches, latch vs switch mechanical action | Chips (`ok`/`running`), `.fb-toggle` | **LED, yes** as a 10 px dot in the tile header for run/stop/stale (Phoebus disconnected border does the same job); mechanical-action semantics (latch-when-released) matter for command buttons: a "stop" that fires once vs a "hold" — worth a `oneshot` flag on command widgets [Inference] |
| Waveform **chart** (strip/scope/sweep update modes, fixed history length, stacked or overlaid plots) vs waveform **graph** (whole array at once) | `TimeSeries`/`MultiSeries` are strip charts with `windowS` + held hour; scope/sweep modes absent; stacked plots absent | **Stacked plots, yes** — a per-source `SourcePanel` already stacks one chart per channel with separate y axes; make x-axis zoom/pan linked across stacked charts (uPlot `cursor.sync`). Scope/sweep: no, they are oscilloscope idioms. Explicit history length as a setting, yes (`useSamples(…, 3600)` is hard-coded) |
| Splitter bars + "Scale all objects with pane" | fixed sections; no user-resizable panes | **No** for the default view; the layout grid in §3 covers resizing. A vertical splitter between chart column and faceplate column is cheap (MUI has none; `react-resizable-panels` is small) — optional |
| Front-panel/block-diagram duality: every indicator is *wired* to a value; hover shows the wire | Links (`Ref`) from tile → channel page, loop → channel | **Borrow the idea, not the diagram** — a "what feeds this" popover on a loop faceplate (channel → loop → actuator) and on an actuator card (which loop drives it) is a one-line query over the schema; a full wiring diagram is not worth it |
| VI Run / Run Continuously / Abort / Pause, with the run arrow doubling as a status light | Programmer has run/stop per program; recording on/off; no global pause | **Pause/resume of a program**, yes if the programmer supports it; "abort" as a distinct destructive action (octagonal/red, confirm) separate from graceful stop — matches the HMI stop-button convention found in §1 |
| Express VIs / DAQ Assistant: wizard that configures a source and drops an indicator | AddLoop stepper dialog; rig file authored by hand | **Partly** — the stepper pattern is already there; a "watch this channel" action that adds a readout/graph panel to the current layout is the useful analogue once §3 exists |
| Property nodes / mechanical action / per-indicator format strings (`%.2f`) | `precision`, `unit`, `range`, `warn`, `alarm` on the channel schema | Already covered by the schema; do not add per-widget format overrides |
| Tab controls, cluster borders, decorations (raised boxes) | MUI Tabs unused on Overview; Paper cards | Tabs for Sources "by source / by unit" are a fine fit; skip decorative bevels |

Not worth borrowing: the fixed-pixel coordinate layout (Ignition's coordinate container is the same idea and both need per-resolution rework), the grey 1990s chrome, and the dataflow diagram as a UI surface.

## 5. Suggested order of work
1. Shell cap + 12-col page grid + spacing tokens (`Shell.tsx`, `theme.tsx`, `app.css`) — one afternoon, fixes the emptiness and alignment.
2. Shared readout grid + `.charts` grid + container-query readout type (`Overview.tsx`, `styles.css`, `Readout.tsx`).
3. Sticky toolbar with segmented chart controls; remove per-section controls (`YScaleSelect.tsx`, `WindowSelect.tsx`, `Shell.tsx`, pages).
4. Quiet colour pass (chips, stats tones, tile borders).
5. Loop faceplate re-layout (`LoopPanel.tsx`, `styles.css`) and loops on Overview.
6. Density toggle, `SectionHead`/`EmptyState` extraction.

Verification I could not do: no browser was opened; the diagnoses in §0 come from reading the CSS/JSX, and the ~1420 px figure matches `maxWidth: 1500 − 2×15 px padding − scrollbar` [Inference].

## 6. What the stress rigs showed (16 Sep 2026)

Seven config-only rigs in `examples/stress/` (see its README for the measured tables) were run through every page. Findings that change the recommendations above:

- **Alarm summary must include channel bands.** `plant.toml` starts with 18 channels outside their warn band; every tile is amber, the app-bar chip and the CONDITIONS stat both read 0 because they count device-level *conditions* only. ISA-101's "alarm summary on every screen" is meaningless if the most common abnormal state — a process value out of band — is not in it. Count = devices with a condition ≥ 30 + channels outside warn (amber) / alarm (red).
- **The sample rate, not the widget count, was the CPU problem.** The "Maximum update depth exceeded" loop the handoff attributed to the dashboard engine was `useStream` calling `setState` once per websocket message; at 60× the furnace alone sends ~240 samples/s and React's nested-update guard trips. Coalescing per animation frame fixed the error but not the cost: `useSamples` still copies every trace on every frame and re-renders from the root. `torrent.toml` (~11 600 samples/s) is the benchmark for the telemetry store in `DESIGN-SPEC.md` §6.
- **A 12-zone furnace makes the Overview a wall of identical tiles** (`rigs-plant-overview.png`): 34 sources × 1 channel in one auto-fill grid with a source heading per tile. For ≥ 12 sources of one unit the readout grid should collapse to the channel table (§3.14) by default, with the tiles one click away — Grafana's "repeat panel" has the same failure mode.
- **Measurand interning.** `zoo.toml` cannot declare a °C `temperature` and a K `temperature`, nor can two sources share `temperature` with different ranges: range/precision/warn/alarm belong to the channel, not the measurand. Left as a design question.
- **Empty states exist and are fine** (`sparse.toml`, `bare.toml` — a rig file can be just a name). `longrun.toml` at 600× fills ten hours of history in a minute and is the right fixture for the history-seeding budget (§6 "History seed").
- **What TOML could not express**: a scripted disturbance mid-program (reader `fail`/`restore`, actuator `disturb`) — done with `scripts/chaos-disturb.sh` against the HTTP routes; a `hold` that times out (only `wait` can). Both are candidates for program steps.

