# flyball dashboard — design specification

Scope: `ui/apps/dashboard` (MUI app) and `ui/packages/react` (library panels, `--fb-*` CSS variables). This document is a specification: every section ends in something an implementer can build and check. It assumes the prior survey (`ux-research.md`) and does not repeat its diagnoses; where it overrides that survey it says so.

Conventions: `[Verified: <source>]` means the claim was read from that source in this session (URL list in §9). `[Unverified]` means I could not read a primary source. `[Inference]` is a reasoned step from verified facts or from the code as read.

What was read in the codebase: `UI.md`, `ui/README.md`, `Shell.tsx`, `theme.tsx`, `app.css`, `PageBar.tsx`, `cards.tsx`, `Status.tsx`, `router.ts`, `App.tsx`, `YScaleSelect.tsx`, `WindowSelect.tsx`, `grouping.tsx`, `icons.tsx`, `pages/Overview.tsx`, `pages/Loops.tsx`, library `styles.css`, `index.ts`, `panels/{Readout,Gauge,LoopPanel,MultiSeries,TimeSeries,ChartOverlay,UnitCharts,EventsPanel,ActuatorPanel}.tsx`, `hooks/{useSources,useStream,useLoops,useEvents,useDevices,useQuery}.ts`, `client/src/wire.ts`, `book/src/6-reference/api.md`, `server/routes/library.py`, and the two screenshots.

---

## 0. The three decisions everything else follows from

1. **Normal is grey; colour is information.** ISA-101 / High-Performance HMI: light-to-medium grey backgrounds, grey outlines for normal equipment, bright colour only for abnormal, and "if everything is coloured, nothing stands out" [Verified: plcprogramming.io ISA-101]. Phoebus goes further: severity is carried by colour *and* line type so colour-blind operators still read it, and the disconnected border can never be turned off [Verified: Phoebus display Readme]. The current app already moved most of the way (quiet `ok` chips, tile borders for warn/alarm). This spec finishes it: series colours are the only place saturated hue appears in a healthy view.
2. **A dashboard is data; the view renders it, the editor edits it, and telemetry never re-renders the tree.** Grafana's dashboard is one JSON with `gridPos` per panel and a `schemaVersion` [Verified: prior survey; Grafana JSON model]; Foxglove's layout is saved server-side with explicit Save/Revert and an "unsaved" indicator [Verified: Foxglove layouts]. flyball follows both, and adds a rule the current `App.tsx` breaks: samples go into a store that widgets subscribe to by channel, not into React state at the root (today `useSamples` sets state on every sample and the whole page re-renders — `App.tsx` even comments on it).
3. **Three levels, not one page.** ISA-101 display hierarchy: Level 1 overview (deviations only), Level 2 unit (loops, KPIs), Level 3 equipment detail (all tags, trends, alarms), Level 4 diagnostics [Verified: plcprogramming.io ISA-101]. Map: dashboards = L1/L2 (composable), detail pages (`#/loops/x`, `#/sources/x/y`, `#/actuators/x`) = L3, Simulation/Events/Sessions = L4. A dashboard widget never tries to be an L3 page; it links to one.

---

## 1. Visual language

### 1.1 Colour tokens

Structure follows Grafana's theme object (text primary/secondary/disabled, background canvas/primary/secondary/elevated, border weak/medium/strong, action hover/selected) [Verified: grafana createColors.ts], because it is the best-tested vocabulary for exactly this kind of UI. Values are flyball's own. Material's dark-theme rules were applied: no pure black, desaturated accents on dark, text emphasis by opacity (87/60/38 %) [Verified: m2.material.io dark theme] — I use 100/68/50/38 because 87 % on a blue-grey canvas measured too low for 13 px readouts.

All contrast figures below were computed with the WCAG formula in this session (script in scratchpad `contrast.mjs`): body text ≥ 6.8:1, tertiary text ≥ 3.7:1, every semantic colour ≥ 4.3:1 against both the panel and the canvas in its mode.

```css
/* packages/react/src/styles.css — replaces the current :root blocks.
   Light is the base; dark overrides under both the OS query and the explicit toggle,
   so the app's data-theme wins in both directions. */
:root {
  color-scheme: light;
  /* background layers, low → high */
  --fb-bg-0: #f2f4f7;              /* canvas (page) */
  --fb-bg-1: #ffffff;              /* panel / card */
  --fb-bg-2: #f6f8fa;              /* inset: readout tiles inside a panel, table stripes, inputs */
  --fb-bg-3: #ffffff;              /* elevated: menus, dialogs, drawers (light uses shadow, see 1.5) */
  --fb-bg-hover: rgba(20, 28, 40, 0.06);
  --fb-bg-selected: rgba(37, 87, 167, 0.10);
  /* borders */
  --fb-border-1: rgba(20, 28, 40, 0.10);   /* weak: card edges, dividers */
  --fb-border-2: rgba(20, 28, 40, 0.18);   /* medium: inputs, hover */
  --fb-border-3: rgba(20, 28, 40, 0.32);   /* strong: focus-adjacent, drag handles */
  /* text ranks */
  --fb-fg: #1b2330;                        /* primary   15.8:1 on bg-1 */
  --fb-fg-2: #4f5a6b;                      /* secondary  7.0:1 */
  --fb-fg-3: #7a8494;                      /* tertiary   3.8:1 — labels ≥ 12px only, never values */
  --fb-fg-disabled: rgba(27, 35, 48, 0.38);
  --fb-fg-on-accent: #ffffff;
  /* accent */
  --fb-accent: #2557a7;                    /* 7.0:1 */
  --fb-accent-hover: #1e4a90;
  --fb-accent-soft: rgba(37, 87, 167, 0.14);
  /* semantic (status) — reserved; never used for series */
  --fb-ok: #1a8a56;        /* 4.4:1. Transitions and positive events only (recording started, program done). */
  --fb-warn: #a8690f;      /* 4.5:1 amber text/border; fill variant below */
  --fb-alarm: #c62828;     /* 5.6:1 */
  --fb-stale: #8a93a2;     /* 3.1:1 — used as a 2px DASHED border and a hatch, never as the only cue */
  --fb-info: var(--fb-accent);
  --fb-warn-fill: rgba(240, 180, 41, 0.18);
  --fb-alarm-fill: rgba(198, 40, 40, 0.14);
  --fb-ok-fill: rgba(26, 138, 86, 0.14);
  /* series — 8 slots, fixed order, validated colour-blind-safe on bg-1 in this mode (see 1.2) */
  --fb-series-1: #2a78d6;  /* blue    */
  --fb-series-2: #d95926;  /* orange  */
  --fb-series-3: #0f9a68;  /* aqua-green */
  --fb-series-4: #b87800;  /* yellow-ochre */
  --fb-series-5: #c9457a;  /* magenta */
  --fb-series-6: #008300;  /* green   */
  --fb-series-7: #4a3aa7;  /* violet  */
  --fb-series-8: #e34948;  /* red     */
  --fb-series-setpoint: var(--fb-fg-2);   /* setpoint/reference overlays are neutral + dashed, not a series slot */
  --fb-series-band: rgba(20, 28, 40, 0.06);/* normal-band shading behind a trace */
  /* chart chrome */
  --fb-grid: rgba(20, 28, 40, 0.08);
  --fb-axis: var(--fb-fg-3);
  --fb-cursor: rgba(20, 28, 40, 0.35);
  /* shape, space, motion (see 1.3–1.6) */
  --fb-radius-1: 4px;  --fb-radius-2: 6px;  --fb-radius-pill: 999px;
  --fb-space-1: 4px; --fb-space-2: 8px; --fb-space-3: 12px; --fb-space-4: 16px; --fb-space-5: 24px; --fb-space-6: 32px;
  --fb-gap: var(--fb-space-3);             /* kept: existing rules read --fb-gap */
  --fb-radius: var(--fb-radius-2);         /* kept: existing rules read --fb-radius */
  --fb-shadow-1: 0 1px 2px rgba(16, 24, 40, 0.06);
  --fb-shadow-2: 0 4px 12px rgba(16, 24, 40, 0.12);
  --fb-shadow-3: 0 12px 32px rgba(16, 24, 40, 0.18);
  --fb-focus: 0 0 0 2px var(--fb-bg-1), 0 0 0 4px var(--fb-accent);
  --fb-dur-1: 120ms; --fb-dur-2: 180ms; --fb-dur-3: 240ms;
  --fb-ease: cubic-bezier(0.2, 0, 0, 1);
  --fb-ease-out: cubic-bezier(0, 0, 0.2, 1);
  /* type (1.3) */
  --fb-font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  --fb-font-mono: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  --fb-text-xs: 11px; --fb-text-sm: 12px; --fb-text-md: 13px; --fb-text-lg: 14px; --fb-text-xl: 16px; --fb-text-2xl: 20px;
  /* legacy aliases so nothing breaks while rules migrate */
  --fb-bg: var(--fb-bg-2); --fb-panel: var(--fb-bg-1); --fb-border: var(--fb-border-1); --fb-muted: var(--fb-fg-2); --fb-error: var(--fb-alarm);
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { /* dark values, identical to the block below */ }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --fb-bg-0: #0e1116;
  --fb-bg-1: #161a21;
  --fb-bg-2: #1d222b;
  --fb-bg-3: #252b35;              /* elevated = lighter, not shadowed (Material dark) */
  --fb-bg-hover: rgba(215, 220, 230, 0.07);
  --fb-bg-selected: rgba(120, 166, 234, 0.14);
  --fb-border-1: rgba(204, 212, 228, 0.10);
  --fb-border-2: rgba(204, 212, 228, 0.18);
  --fb-border-3: rgba(204, 212, 228, 0.30);
  --fb-fg: #d7dce6;                /* 12.7:1 on bg-1 */
  --fb-fg-2: #9aa3b2;              /* 6.9:1 */
  --fb-fg-3: #6f7887;              /* 3.9:1 */
  --fb-fg-disabled: rgba(215, 220, 230, 0.38);
  --fb-fg-on-accent: #0e1116;
  --fb-accent: #78a6ea;            /* 7.0:1 */
  --fb-accent-hover: #93b8f0;
  --fb-accent-soft: rgba(120, 166, 234, 0.16);
  --fb-ok: #3fb27f;      /* 6.6:1 */
  --fb-warn: #f0b429;    /* 9.4:1 */
  --fb-alarm: #f0616a;   /* 5.5:1 */
  --fb-stale: #7d8696;   /* 4.8:1 */
  --fb-warn-fill: rgba(240, 180, 41, 0.16);
  --fb-alarm-fill: rgba(240, 97, 106, 0.16);
  --fb-ok-fill: rgba(63, 178, 127, 0.14);
  --fb-series-1: #3987e5;
  --fb-series-2: #d95926;
  --fb-series-3: #199e70;
  --fb-series-4: #c98500;
  --fb-series-5: #d55181;
  --fb-series-6: #008300;
  --fb-series-7: #9085e9;
  --fb-series-8: #e66767;
  --fb-series-band: rgba(204, 212, 228, 0.06);
  --fb-grid: rgba(204, 212, 228, 0.08);
  --fb-cursor: rgba(204, 212, 228, 0.35);
  --fb-shadow-1: 0 1px 2px rgba(0, 0, 0, 0.35);
  --fb-shadow-2: 0 4px 12px rgba(0, 0, 0, 0.45);
  --fb-shadow-3: 0 12px 32px rgba(0, 0, 0, 0.55);
  --fb-focus: 0 0 0 2px var(--fb-bg-1), 0 0 0 4px var(--fb-accent);
}
@media (prefers-reduced-motion: reduce) { :root { --fb-dur-1: 0ms; --fb-dur-2: 0ms; --fb-dur-3: 0ms; } }
```

App side: `theme.tsx`'s `FbVars` currently writes `--fb-*` from the MUI palette. Invert it: the library CSS owns the tokens; `makeTheme(mode)` **reads** them (a tiny `token(name)` helper doing `getComputedStyle(document.documentElement).getPropertyValue`) so MUI's `palette.background.paper` = `--fb-bg-1`, `divider` = `--fb-border-1`, `text.secondary` = `--fb-fg-2`, and so on. One source of truth, and an embedder with no MUI gets the same look. `AppTheme` sets `data-theme` on `<html>`; the existing `useThemeVersion` MutationObserver in `TimeSeries.tsx` already rebuilds charts when that attribute changes.

Where the chips in the app bar today use MUI `color="success"` for healthy states (`Status.tsx`: readers running, recording, loops regulating, program running), change to `color="default"` outlined; keep `warning`/`error` for abnormal. Recording gets a small red dot (universal "REC" cue) rather than green.

### 1.2 Series palette — what was validated and what it obliges

The eight series colours are the dataviz skill's reference categorical palette, re-stepped for light mode, and were run through its validator (`validate_palette.js`: lightness band, chroma floor, adjacent-pair colour-blind ΔE, normal-vision ΔE, contrast vs surface) against flyball's own surfaces:

| Palette | Surface | Result |
|---|---|---|
| Existing `--fb-series-1…6` | dark `#161a21` | FAIL: `#2557a7` outside lightness band; `#0e7490` reads grey; series 1 and 4 below 3:1 contrast |
| Existing `--fb-series-1…6` | light `#ffffff` | FAIL: `#0e7490` chroma; green↔orange CVD ΔE 7.0 (warn band) |
| Okabe-Ito 7, Tol bright 7, Tol vibrant 7 | both | FAIL lightness band on dark (too light) and contrast on light — designed for white paper, not both modes |
| **Dark set above** | `#161a21` and `#1d222b` | **PASS all five**; worst adjacent CVD ΔE 8.4, normal ΔE 19.3 |
| **Light set above** | `#ffffff` | **PASS all five**; worst adjacent CVD ΔE 8.6, normal ΔE 18.7, all ≥ 3:1 |
| Light set | `#f2f4f7` canvas | PASS with one CVD warn (7.4) — charts always sit on `--fb-bg-1`, so this is the fallback case only |

Obligations the validator attaches: colour is assigned in **fixed slot order by entity**, never re-cycled when a series is hidden (a filter must not repaint survivors), a **legend is always present for ≥ 2 series** (uPlot's legend stays on), and a 9th series folds into "more…" or a second chart rather than a generated hue. Setpoint/reference traces are not slots: they are `--fb-series-setpoint` dashed (as `LoopPanel` already does with `dash: true`), so an operator learns "dashed grey = target" once.

Okabe-Ito hex values for reference (`#E69F00 #56B4E9 #009E73 #F0E442 #0072B2 #D55E00 #CC79A7`) [Verified: scifig.ai / conceptviz references] and Paul Tol's bright/vibrant/muted sets [Verified: sronpersonalpages.nl/~pault] were the candidates; both fail the dark surface without re-stepping, which is why they are not used verbatim.

### 1.3 Typography

- **Family.** Keep `system-ui` (already the theme's choice; zero bytes). If a bundled face is wanted for identical rendering on the Pi and a laptop, self-host **Inter** variable from the daemon's static bundle and enable `font-feature-settings: "tnum" 1, "ss01" 1` — Inter's `tnum` gives fixed-width digits and `ss01` "open digits" made for data [Verified: rsms.me/inter via search summary]. Do not load from a CDN: the daemon may be offline. Mono (`--fb-font-mono`) only for raw JSON/YAML and event `details`.
- **Tabular figures everywhere a number can change.** `font-variant-numeric: tabular-nums` is already on `body`; keep it, and keep the `minWidth: ${width}ch` reservation in `Readout`/`Gauge` (`numberWidth`) so digits do not jitter.
- **Scale** (px; line-height 1.25 for readouts, 1.45 for body):

| Rank | Size | Weight | Use |
|---|---|---|---|
| label | 11 uppercase, letter-spacing .06em, `--fb-fg-2` | 600 | section heads, `dt` labels, chip text |
| caption | 12, `--fb-fg-2` | 400 | units under gauges, timestamps, legend |
| body | 13, `--fb-fg` | 400 | everything else (MUI `fontSize: 13` stays) |
| body-strong | 13 | 600 | names in card headers |
| title | 14 | 600 | panel titles |
| page | 16 | 600 | app-bar page title |
| readout-s | 20 | 500 | loop faceplate PV/SP/OP, stat tiles |
| readout-m | `clamp(22px, 11cqi, 40px)` | 500 | readout tile value (container-query sized, as today) |
| readout-l | `clamp(32px, 14cqi, 72px)` | 500 | "big number" widget (≥ 6 columns) |
| unit | 0.5em of its value, `--fb-fg-2`, baseline-aligned | 400 | unit suffix |

Weight 500 for numerals: 600+ at large sizes fills the counters and hurts 6/8/9 legibility on dark; 400 is too thin for the value to lead. [Inference]

### 1.4 Spacing and radii

One scale, 4-based (`--fb-space-1…6` = 4/8/12/16/24/32). MUI theme `spacing: 4` so `p: 2` = 8, `p: 3` = 12 (today it is 6, producing 6/9/12/15 and the misalignment the survey found). Rules:

- card padding 12; gutter between cards 12 (dense) / 16 (comfortable); section gap 24; page gutter 16 (phone) / 24 (desktop) — the values `app.css` already names, made the only values.
- inside a widget: title row → body 8; body → footer 8.
- radii: 4 controls/inputs/chips, 6 cards and widgets, pill for status chips. Nothing larger: large radii read as consumer, and they eat corner space in dense grids.

### 1.5 Elevation

- Light: cards are `bg-1` with a 1px `border-1`; no shadow. Overlays (menus, dialogs, widget picker) `bg-3` + `shadow-2`; the chart overlay `shadow-3`.
- Dark: cards are `bg-1` on `bg-0` canvas with `border-1`; overlays are `bg-3` (lighter surface = higher, per Material dark theme [Verified: m2.material.io]) plus `shadow-2` for edge definition only.
- Inset (`bg-2`): readout tiles inside a panel, inputs, table header — one step *into* the card. Never nest more than two levels (`bg-1 › bg-2`); the current `LoopPanel` nests three (`fb-panel › fb-loop-readout › .fb-num` bg) and reads muddy.

### 1.6 Focus, hover, selection

- `:focus-visible` only: `box-shadow: var(--fb-focus)` (2px surface gap + 2px accent ring — visible on both modes and on coloured borders). Never `outline: none` without it. The existing `.fb-form input:focus-visible` rules migrate to this token.
- Hover on clickable cards: `border-color: var(--fb-border-2); background: var(--fb-bg-hover)`, 120 ms. Selected widget in edit mode: 2px accent border (Foxglove marks the selected panel with a coloured border while its settings sidebar is open [Verified: Foxglove panels]).

### 1.7 Motion

Durations 120/180/240 ms, ease `cubic-bezier(.2,0,0,1)`; the 160–240 ms band is the routine-UI norm and reduced-motion collapses tokens to 0 ms [Verified: designsystems.one duration & easing via search summary]. What animates:

| Animates | Never animates |
|---|---|
| hover/focus colour (120) | numeric values (no counting-up; a new sample replaces the old text) |
| menu/dialog/drawer open (180 fade+4px rise) | chart data (uPlot redraws; no tween) |
| widget enter on add (240 fade), placeholder move in edit mode (RGL's own 200 ms transform) | gauge needles/fills beyond 120 ms — a fast-moving process value must not lag its number |
| a single 600 ms border pulse when a tile *enters* warn/alarm (not while it stays there) | anything continuous (no blinking; PlantPAx flashes for unacknowledged alarms [Verified: PROCES-RM200], but flyball has no acknowledge concept yet — do not flash) |

### 1.8 Iconography

Keep `@mui/icons-material` in the app (already imported) at 16 px inline / 20 px in nav; the library stays icon-free except inline SVG it draws itself (gauges). Rules: an icon never carries meaning alone — status = icon + text (ISA-101 dual coding [Verified: plcprogramming.io]); one icon per concept, fixed: sources `Sensors`, channel by dimension (`channelIcon` as today), actuator `Tune`, loop `Loop`, program `PlaylistPlay`, events `NotificationsNone`, sessions `Storage`, sim `Science`, recording `FiberManualRecord` (red when on), stale `SyncDisabled`, warn `WarningAmber`, alarm `ErrorOutline`, ok `CheckCircleOutline` (only in transitions/toasts).

---

## 2. Page anatomy

```
┌─────────┬──────────────────────────────────────────────────────────────────────────────┐
│ flyball │ Overview ▾   [dashboard tabs: Overview · Furnace · Tuning] [+]   ⏺ rec  ⚠2  ◐ sim×60  ☾ │  app bar 48
│ ▣ Dash… ├──────────────────────────────────────────────────────────────────────────────┤
│ ∿ Sources│ [Edit] [⟳ live ▾ 5m]  [window 1m 5m 15m 1h] [sample all ½ ⅕] [y auto range] │  page bar 44 (sticky)
│ ⊟ Actu… ├──────────────────────────────────────────────────────────────────────────────┤
│ ↻ Loops │ ┌ widget ─────────────┐ ┌ widget ────┐ ┌ widget ────────────────────────────┐ │
│ ▷ Progr…│ │ title     unit  ● ⋯ │ │            │ │                                    │ │
│ ⚑ Events│ │                     │ │            │ │                                    │ │
│ ▤ Sessi…│ └─────────────────────┘ └────────────┘ └────────────────────────────────────┘ │
│ ⚗ Sim   │                                                                              │
└─────────┴──────────────────────────────────────────────────────────────────────────────┘
```

**App bar (48 px, `bg-1`, bottom `border-1`).** Left: page title (16/600). Centre-left on the Dashboards page: dashboard switcher (a `Select`-styled button "Overview ▾" listing this rig's dashboards + "Manage…"), because Grafana and HA both put dashboard identity at the top, not in the sidebar. Right, in this order: **alarm summary** (ISA-101 wants an alarm summary bar on every screen [Verified: plcprogramming.io]) as one chip `⚠ 2` = count of active conditions at level ≥ 30, red if any ≥ 40, grey `⚠ 0` when none, always present, click → Events filtered; recording chip (red dot + session name, or grey "not recording"); program chip (only while running: "ramp · step 6/11"); sim chip (only when speed ≠ 1); stream health folded into one chip `● live` that turns amber "reconnecting" / red "offline" (today two separate `samples`/`actuators` chips cost 200 px for a state that is almost always "open"); theme toggle. The chips scroll horizontally on a phone (already done).

**Sidebar (196 / 56 mini / drawer on phone — keep).** First item becomes **Dashboards** (icon `DashboardOutlined`, replaces "Overview"); the rest as today. Active item: 3px left accent bar (keep). Add a bottom-pinned item "Edit rig file" only when sim is attached (L4).

**Page bar (44 px, sticky under the app bar — keep `PageBar`).** Left: page actions (Edit layout / Add loop / Start recording). Right: the chart controls once per page (already done). Rule: segmented controls are 28 px tall, labelled by a 11 px caption to their left (`Labelled`), so the bar is a single baseline. In dashboard **edit mode** the bar changes state (see §4): it turns `--fb-accent-soft`, and shows `+ Add widget · Undo · Redo · Save ▾ · Discard · Exit`.

**Section headers** (on non-dashboard pages): 11 px uppercase `--fb-fg-2` title + muted count (`Sources · 4`) + optional right-aligned text button; min-height 32; 8 px below. One component `SectionHead` in `cards.tsx`, used on every page (the survey's P3; still open).

**Widget / panel chrome** (one component `PanelFrame` in the library, used by every widget and by the existing panels):

```
┌──────────────────────────────────────────────┐
│ ● Title                     unit   ⤢  ⋯      │   title row 28px: 12px status dot · 14/600 title · 12px unit/subtitle · actions on hover
│ ─ body ─────────────────────────────────── │
│                                              │
│ footer: 12px fg-2 (e.g. "5m · 1/2" or link) │   optional
└──────────────────────────────────────────────┘
```

- Status dot (8 px) replaces per-card chips: grey (normal), amber, red, hollow-dashed grey (stale/no data). Tooltip names the condition. Chips stay only where text matters (mode `regulating`).
- Border carries severity as today: `2px solid --fb-warn`, `2px double --fb-alarm`, `2px dashed --fb-stale` — colour + line type per Phoebus [Verified: Phoebus Readme]. Body colour never changes; the value text may.
- Actions (`⤢` expand to overlay, `⋯` menu: open detail page, duplicate/remove in edit mode) appear at 35 % opacity, 100 % on hover/focus-within — the pattern `ChartToolbar` already uses.
- Title is the widget's `title` or the generated one ("zone1.temperature"); a `Ref` link when it names a thing with a page.

**Density.** Two settings, `comfortable` (default) and `compact`, stored beside the theme. Compact changes only: `--fb-gap` 8, widget title row 24, readout tile min-height, chart `height` clamp lower bound 140, MUI `Table size="small"` already. Grafana ships no density toggle; Kibana's dashboard settings toggle panel titles/borders/margins [Verified: elastic.co create-dashboard]; HA has "dense placement" [Verified: HA sections]. One toggle is enough.

**Empty / loading / error states** (one `StateBlock` component, dashed `border-2`, centred, `bg-2`):

| State | Content | Example |
|---|---|---|
| loading | 12 px "loading…" + skeleton bars (no spinner for < 400 ms; skeleton after) | dashboard fetch |
| empty (no data yet) | value "—", sparkline empty, status dot hollow; no message | readout before first sample |
| empty (nothing configured) | one sentence + primary action | "No loops attached. [Add loop]" |
| unbound (dashboard references a thing the rig lacks) | `⚠ zone4.temperature is not on this rig` + [Rebind] [Remove] in edit mode, [Hide] in view | see §4.8 |
| error | `--fb-alarm-fill` band, message, [Retry] | route 5xx |
| stale | last value stays visible, dashed border, footer "last sample 42 s ago" | reader stopped |

"Stale" is decided per channel: no sample for > max(3 × reader period, 5 s) — the reader's `period_s` is on `ReaderRun`.

---

## 3. Widget catalogue

Common to every widget: `id`, `kind`, `title?` (default generated), `x y w h` on a 24-column grid (§5), `config` per kind, and a **binding** — implemented as fields inside `config` (`channel`, `channels`, `loop`, `actuator`…), not a separate `bind` object as the JSON in §5 sketches — that is one of `channel {source, measurand}`, `channels[]`, `loop`, `actuator`, `reader`, `program`, or none. Cost class: **cheap** = DOM text/SVG updated ≤ 2 Hz from the store; **chart** = one uPlot instance (`setData` ≤ 10 Hz, paused off-screen); **heavy** = several charts or a table with live rows. Sizes are `w×h` in grid cells (24 cols; row = 24 px + 12 px gap, so h=6 ≈ 204 px).

Grid sizing in words: "1/4 row" = 6 cols, "1/3" = 8, "1/2" = 12, "full" = 24.

### 3.1 Readout — `readout` (cheap; sparkline makes it *chart*)
Purpose: one channel at a glance: big number, unit, position in range, recent trend. ISA-101's analog indicator: value, scale, normal band, a small sparkline beside it [Verified: plcprogramming.io]; Grafana stat's "sparkline in background" and "colour mode: value / none" [Verified: Grafana stat].
Binds: `channel`. Default 6×5; min 4×3 (below 4 wide the sparkline is dropped automatically).
Config: `sparkline: true`, `sparklineS: 300`, `showRange: true`, `showSource: true`, `colorMode: "value" | "none"` (value text takes warn/alarm colour, default `value`), `decimals?` (override channel precision), `size: "auto" | "s" | "m" | "l"`.
```
┌ temperature · zone1            ● ⋯ ┐
│  654.7 °C                          │  readout-m, unit at .5em
│  ▮▮▮▮▮▮▮▮▮▮▮▮▮▯▯▯▯▯ |w   |a        │  range bar: fill + warn/alarm ticks (exists)
│  ╭─╮_╭──╮___╭╮__╭─╮                │  sparkline 44px, no axes (exists: TimeSeries compact)
└────────────────────────────────────┘
```
Existing `Readout.tsx` is this widget; add `colorMode`, the stale state, and make the sparkline a store-subscribed chart (§6).

### 3.2 Gauge — `gauge` (cheap)
Purpose: a picture of where the value is in its range, with zones. Node-RED offers tile / half / ¾ / battery / tank [Verified: dashboard.flowfuse.com ui-gauge]; Grafana's gauge has circle/arc, thresholds shown as an outer band, neutral point, segments [Verified: Grafana gauge]; PlantPAx colour-codes active alarms directly on the PV's linear gauge [Verified: PROCES-RM200 "Linear Gauge Alarm Colors"].
Binds: `channel` (or a loop's PV with SP marker: `loop`).
Default 6×6; min 4×4 (bar: 6×3, min 4×2).
Config: `kind: "auto" | "dial" | "arc" | "bar" | "thermometer" | "tank"` (auto = `gaugeKindFor(unit)` as today), `showValue: true`, `showTicks: true`, `zones: "channel" | "none"`, `neutral?: number` (fill from this value rather than min; Grafana's neutral point), `setpoint?: true` when bound to a loop (draws SP as a notch).
```
   dial                     bar
   ╭──────╮               ┌────────────────────┐
  ╱ w  ok  w╲             │▮▮▮▮▮▮▮▮▮▮▮▮▯▯▯▯▯▯▯▯│
 │    ╱      │            │0        |w      |a │
 │   ●       │            │        654.7 °C    │
  ╲a       a╱             └────────────────────┘
   654.7 °C
```
Zones are neutral grey when the channel has no bands (as `Gauge.tsx` does); coloured zone *arcs* are muted fills (`--fb-warn-fill`/`--fb-alarm-fill`), the needle/fill takes the strong colour only when inside that zone — grey-is-normal.

### 3.3 Time-series chart — `chart` (chart)
Purpose: several channels of one unit on one axis, live and scrollable into history (UI.md B8). Grafana time-series options were the checklist: line style, fill/gradient, points auto/always/never, soft min/max, legend list/table + placement, tooltip single/all, connect-nulls [Verified: Grafana time-series]; Foxglove downsamples min/max/first/last and syncs the timeline across plots [Verified: Foxglove plot].
Binds: `channels[]` (1–8; all must share a unit — the picker enforces it; a second unit is a second chart, never a dual axis), optional `overlays[]` of `{loop, trace: "setpoint" | "demand" | "expected"}` drawn dashed in `--fb-series-setpoint` when the loop's unit matches.
Default 12×8; min 8×5.
Config: `windowS?` (null = follow page bar), `yScale: "auto" | "range" | {min,max}`, `softMin?/softMax?`, `legend: "bottom" | "right" | "none"`, `legendValues: ["last"]`, `lineWidth: 1.5`, `fill: 0` (0–0.3 opacity; area fill for single series only), `points: "auto"`, `syncGroup: "page"` (uPlot `cursor.sync.key` [Verified: uPlot d.ts]; every chart on a dashboard shares the page key by default so hovering one shows the time on all — Foxglove "sync with other plots"), `showBands: true` (shade the widest warn band of the bound channels with `--fb-series-band`).
```
┌ °C  zone1 · zone2 · zone3 · sample      ◀ ▶ − + fit  live ⤢ ┐
│ 670 ┤‾‾‾‾‾‾‾‾‾‾╲___                                          │
│ 650 ┤━━━━━━━━━━━━━━━━━━╲__                                   │  band shaded
│ 630 ┤            ‐ ‐ ‐ ‐ ‐ ‐ setpoint (dashed, grey)           │
│     └──┬─────┬─────┬─────┬─────┬─────┬──                     │
│      3:51  3:52  3:53  3:54  3:55                             │
│ ■ zone1 654.7  ■ zone2 655.1  ■ zone3 604.5  ■ sample 661.2   │  legend with last values
└──────────────────────────────────────────────────────────────┘
```
`MultiSeries.tsx` is the renderer; it needs the store subscription (no `series` prop churn), the sync key, bands, and legend values.

### 3.4 Loop faceplate — `loop` (chart if trends on; cheap otherwise)
Purpose: the L2 view of a controller: PV/SP/OP, mode, deviation, and the controls that change them. Conventions from PlantPAx: alarm banner at the top of the faceplate showing only the most important condition; tab border coloured by highest severity [Verified: PROCES-RM200]; ISA-101 deviation bar with the setpoint centred [Verified: prior survey]. The survey's P2 faceplate layout stands; this adds the widget form.
Binds: `loop`.
Default 8×8 (`trends: true`) / 6×5 (`trends: false`); min 6×4.
Config: `trends: true` (process + drive mini-charts stacked at the right, ~110 px each, bare uPlot: no axes, legend, cursor or toolbar — the values are in the PV/SP/OP rows), `controls: true` (setpoint entry, Regulate/Move/Stop — hidden in view mode when the dashboard is marked `readOnly`), `showLaw: false`, `showFeedforward: false` (these belong on the L3 page).
```
┌ heater1  zone1.temperature   [REGULATING]         ● ⋯ ┐
│ ⚠ actuator at limit                                   │  banner only when a condition exists
│ PV  629.9 °C  ▮▮▮▮▮▮▮▮▮▮▮◆▯▯▯▯▯   dev −0.6           │  ◆ = SP notch on the PV bar
│ SP  630.5 °C  [ 630.5 ] [Move]                        │
│ OP 1134.2 W   ▮▮▮▮▮▮▮▯▯▯▯▯▯▯▯▯▯  achievable 1134.2    │
│ ╭─╮_╭╮ process   ╭╮╭╮╭╮ drive     [Stop]              │  optional trends
└───────────────────────────────────────────────────────┘
```
Deviation = PV − SP in the channel's unit, `--fb-warn` beyond the warn band, otherwise `--fb-fg-2`. The mode badge is the one place a text chip stays coloured: `regulating` accent-soft, `open` warn-fill, `manual` grey.

### 3.5 Actuator card — `actuator` (cheap)
Purpose: state fields + the commands that matter here. HA's tile card: icon + name + state with "features" (inline buttons/sliders) at the bottom [Verified: HA tile]; Perspective symbols carry running/stopped/faulted states [Unverified — the symbols page 404'd; from the search summary only].
Binds: `actuator`.
Default 6×5; min 4×3.
Config: `fields: string[] | "all"` (state fields to show; default the first 4 numeric ones), `commands: string[]` (tags to render inline; default none — commands live on the L3 page; a dashboard exposes chosen ones), `compact: true` (inline one-line forms: for a command whose arguments are ≤ 2 numbers render field + button; otherwise a button opening the `CommandForm` in a popover).
```
┌ heater1  SimActuator                 ● ⋯ ┐
│ demand   1167.6 W   ▮▮▮▮▮▮▮▯▯▯          │
│ input     0.47 of full                    │
│ [ set  ____ W ] [Apply]   [Off]           │  chosen commands
└───────────────────────────────────────────┘
```

### 3.6 Program status — `program` (cheap)
Purpose: what the programmer is doing (running/idle, command tag, step i/n) with the step list and progress. Bluesky's SOPHYS GUI shows RE state + environment state, a progress bar, and buttons that change by state (start when idle; pause/stop when running; abort/halt/resume when paused) [Verified: github cnpem/sophys-gui]; the queue-server model is idle / executing_queue / paused with pause-deferred/immediate, resume, stop, abort, halt [Verified: bluesky-queueserver introduction].
Binds: `program?` (a library program name to run) or none (whatever is running).
Default 8×6; min 6×3.
Config: `showSteps: true`, `controls: true`, `programs: string[]` (quick-run buttons).
```
┌ program  ramp                 RUNNING  ● ⋯ ┐
│ step 6 / 11  ▮▮▮▮▮▮▯▯▯▯▯   set_reference    │
│  ✓ 1 regulate at 600            ✓ 2 wait 5m │
│  ▶ 6 set_reference 650  … 7 wait 10m        │
│ [Interrupt]                                 │   idle: [Run ▾]
└─────────────────────────────────────────────┘
```
Polls `/api/programs/running` at 1 s while running, 5 s idle (as `App.tsx` does) — move that poll into the store so several widgets share one.

### 3.7 Events feed — `events` (cheap; heavy if unfiltered at high rate)
Purpose: recent events for a scope. Ignition's alarm status table conventions: row style by state and priority, filters by state/priority + free text, sort on state/priority descending [Verified: Ignition alarm status table].
Binds: none, or `scope` filter (`{scope: "loop", subject: "heater1"}`).
Default 12×6; min 6×3.
Config: `levels: ["WARNING","ERROR"]`, `limit: 50`, `columns: ["time","level","scope","message"]`, `filter?: string`.
Reuses `EventsPanel`; cap DOM rows to `limit`; new rows prepend without re-rendering old ones (keyed rows).

### 3.8 Condition summary — `conditions` (cheap)
Purpose: the alarm summary as a widget (the app-bar chip is the always-on version). Shows active conditions across readers/actuators/writers from `/api/health` sorted by level desc then age; empty state is the point: "ideally empty" [Verified: prior survey, Phoebus alarm table].
Binds: none. Default 6×4; min 4×2.
Config: `minLevel: 30`, `groupBy: "device" | "none"`.
```
┌ conditions                    2 active ● ⋯ ┐
│ ● ERROR   zone3  offline        since 4m   │
│ ● WARNING heater2 at limit      since 40s  │
└────────────────────────────────────────────┘
```

### 3.9 Rig health tiles — `health` (cheap)
Purpose: the L1 KPI strip: rig ok/fault, recording, readers n/m, loops regulating, conditions, uptime — the current `Stat` row, as one widget with selectable tiles.
Binds: none. Default 24×2; min 6×2.
Config: `tiles: ["rig","recording","readers","loops","conditions","signals","events","uptime"]` (order = display order), `layout: "row" | "grid"`.

### 3.10 Session recording control — `recording` (cheap)
Purpose: start/stop/name the recording, see duration and size. Backend routes for start/stop are UI.md B9; until they land the widget shows state and links to Sessions.
Binds: none. Default 6×3; min 4×2.
Config: `showLast: 3` (recent sessions as links), `confirmStop: true`.
```
┌ recording                         ⏺ ● ⋯ ┐
│ session #19 "ramp test"   1h 12m  4.2 MB │
│ [ name ________ ] [Stop]   [flag ⚑]      │
└──────────────────────────────────────────┘
```

### 3.11 Text / markdown — `text` (cheap; the implemented kind name is `text`, not `markdown`)
Purpose: procedure notes, warnings, links; Chronograf's Note cell and Node-RED markdown [Verified: influxdata visualization types; flowfuse widgets]. Rendered with a small sanitising Markdown renderer (no HTML pass-through).
Binds: none. Default 6×4; min 3×2. Config: `text`, `align: "left"`.

### 3.12 Heading / spacer — `heading` (cheap)
Purpose: a section title across the grid (HA's heading card; Grafana rows). Height 1 cell; full width by default; `spacer: true` renders nothing (HA/Node-RED spacer).
Binds: none. Default 24×1; min 2×1. Config: `text?`, `spacer?: boolean`, `collapsible?: false`.

### 3.13 Link / button — `link` (cheap)
Purpose: jump to an L3 page or another dashboard; optionally run one command with confirmation (a "Stop all" button). Config: `target: {page, name?} | {dashboard} | {command: {actuator, tag, args}}`, `label`, `icon?`, `variant: "link" | "button" | "danger"`, `confirm?: string`. Default 4×1; min 2×1. Danger buttons are outlined red, 44 px tall minimum for touch [Verified: plcprogramming.io ISA-101 44×44 px].

### 3.14 Channel table — `table` (cheap ≤ 30 rows; heavy above)
Purpose: many channels in little space: name · value · unit · range bar · status. Grafana table / Ignition alarm table columns model.
Binds: `channels[]` or `sources[]` or `"all"`. Default 8×6; min 6×3.
Config: `columns: ["channel","value","unit","bar","sparkline","status","updated"]`, `sortBy: "source"`, `groupBySource: true`, `sparkline: false` (each sparkline is a chart instance; default off; on = heavy).

### 3.15 Image — `image` (cheap)
Purpose: a P&ID/photo of the rig with optional readout pins. Config: `src` (data: URI or a path the daemon serves), `fit: "contain"`, `pins: [{x%, y%, channel}]` (a mini readout anchored on the image). Default 12×8; min 4×3. Pins make it chart-free but each pin is a store subscription.

### 3.16 Not in v1 (deliberately)
Stacked/"scope" chart modes, XY plots, per-widget time ranges (Kibana has them; they confuse a live control page), knobs, dual axes.

---

## 4. Dashboard editor UX

Prior art each decision comes from is cited inline. Terms: **view mode** (default) and **edit mode** (Grafana's Edit button on the toolbar; HA's pencil top-right [Verified: HA sections]).

### 4.1 Entering and leaving edit mode
- Page bar shows **Edit** (keyboard `e` while no widget is focused — Grafana uses `e` for panel edit [Verified: Grafana use-dashboards shortcuts]). Edit mode: page bar turns `--fb-accent-soft` with "Editing *Furnace*" and the actions `+ Add widget · ↶ ↷ · Save ▾ · Discard · Done`. Widgets show a 6 px drag handle strip in their title row and a resize corner; body interactions are disabled (`pointer-events: none` on bodies, so a stray click cannot send a command mid-edit).
- **Done** with unsaved changes asks: Save / Discard / Keep editing (Foxglove tracks "unsaved" locally and offers Save/Revert [Verified: Foxglove layouts]).
- `Esc` exits a picker/dialog, then exits edit mode (Grafana `Esc` semantics).

### 4.2 The grid
- 24 columns, row height 24 px, gap 12 px (16 in comfortable density), vertical compaction ("negative gravity": panels float up — Grafana; RGL `verticalCompactor`). One layout; no per-breakpoint layouts. Below 900 px view mode stacks widgets full-width in `(y, x)` order — HA rejected masonry and per-size layouts for exactly the "muscle memory" reason [Verified: prior survey HA chapter 1].
- **View mode renders plain CSS grid** (`grid-column: x+1 / span w; grid-row: y+1 / span h`) — no drag library mounted, no transforms, no ResizeObserver per item. **Edit mode mounts react-grid-layout** over the same widget elements. This is the single largest processing saving: the library costs nothing while operators watch.

### 4.3 Adding a widget
`+ Add widget` opens a dialog with two tabs, copied from HA 2026.6's card picker, which opens on **"By entity"** (a tree of the things you have; picking one shows *live previews* of the cards that make sense for it) with **"By card"** as the second tab; on mobile it is a two-step flow [Verified: HA 2026.6 release post].

- **By thing** (default): left column = the rig schema as a tree: Sources › source › channels; Loops; Actuators; Readers; Rig (health, recording, program, events). Search box filters flat. Selecting a thing shows on the right the widgets that accept that binding, each as a **real preview rendered from the store** (a readout of zone1 with its live value, a gauge, a chart, a table row), with a one-line description. Click adds it at the first free slot at the bottom (Grafana adds at top; HA adds at the end of the section — end is better for a live page because nothing above moves).
- **By widget**: the catalogue grouped Readouts / Charts / Control / Status / Layout with search; picking one then asks for its binding using the same tree, pre-filtered to compatible kinds (a chart offers only channels; picking channels of a second unit is refused with "one unit per chart — add another chart").
- Foxglove alternative (drag from a topic list into a panel) is not used: dragging onto a live page is easy to do by accident on a touch screen.

### 4.4 Binding and configuring
Clicking a widget in edit mode selects it (2 px accent border) and opens a **right-hand settings pane** (Foxglove's settings sidebar: click another panel and the pane follows [Verified: Foxglove panels]; Grafana's edit pane [Unverified — the edit-dashboards page 404'd twice]). The pane is the existing `SchemaForm` pointed at the widget kind's config JSON Schema (UI.md §3 "the form component pointed at the layout schema"), plus a **binding field** whose widget is the rig-schema tree picker. Title is a text field with placeholder = the generated title. Changes apply live to the widget behind the pane (Grafana/HA both preview live).

### 4.5 Moving, resizing, duplicating, removing
- Drag by the title strip; resize by the corner. RGL's placeholder shows the landing cell; compaction runs on drop (`onDragStop`/`onResizeStop`, not on every move).
- Widget menu (`⋯`) in edit mode: Duplicate (Grafana `pd`), Remove (`pr`), Move to top, Bring to front (only meaningful with `allowOverlap`, which we do not enable). Keyboard: with a widget focused, arrow keys move by one cell, `Shift+arrows` resize, `Delete` removes, `Ctrl/⌘+D` duplicates — RGL v2 exposes `moveItem`/`resizeItem` actions for this [Verified: RGL v2 RFC].
- Min sizes per kind (§3) are enforced as RGL `minW/minH`.

### 4.6 Undo / redo
The dashboard document is immutable; every edit pushes onto a history stack (cap 100). `Ctrl/⌘+Z` / `Shift+Ctrl/⌘+Z`; the buttons show the stack depth in a tooltip. HA and Grafana both provide undo in their editors [Unverified for HA's sections editor beyond the search summary; Grafana's version history is a save-level undo]. Persist the unsaved draft to `sessionStorage` keyed by dashboard id so a reload does not lose it (Foxglove keeps unsaved changes local).

### 4.7 Saving, naming, switching
Menu `Save ▾`: **Save** (Ctrl/⌘+S, Grafana), **Save as…** (new name; copies), **Rename**, **Set as rig default**, **Delete** (confirm; refuses to delete the rig default — pick another first), **Export JSON**, **Import JSON…**. Each save is a new version server-side (the `tuning`/`program` tables already do append-only history; Grafana increments `version` per save). Foxglove's org-layout flow — "Make a personal copy" when editing a shared layout — maps to: the generated `Overview` is not editable in place; editing it prompts "Save as…" (HA: the auto-generated dashboard is regenerated until you take control; after that it is yours [Verified: prior survey]).

Switching: the app-bar switcher lists the rig's dashboards (default first, then alphabetical), with the URL `#/dashboards/<name>` so a dashboard is shareable and the browser back button works. Last-opened dashboard per rig in `localStorage` (ephemeral convenience — UI.md rule).

### 4.8 When a dashboard references something the rig lacks
Server validation returns `problems: [{widgetId, ref, reason}]` alongside the document rather than refusing it (UI.md §4 said "fails validation on load"; I recommend load-with-problems, because a rig with one renamed sensor should not lose a 20-widget dashboard). The widget renders the `unbound` state (§2 table): dashed border, `⚠ zone4.temperature is not on this rig`, and in edit mode **Rebind** (opens the tree picker filtered to the same unit/dimension, pre-selecting the closest name) or **Remove**. The page bar shows "3 widgets need attention" until resolved. The Phoebus rule — missing data is always visibly indicated and cannot be hidden [Verified: Phoebus Readme] — applies: view mode shows the stub, never silently collapses it.

### 4.9 Import / export
Export downloads `<rig>-<name>.json` (§5 shape, pretty-printed). Import accepts the same, validates `schema_version` (migrating older versions in the client), shows the problems list before committing, and saves as a new dashboard named from the file (rename on conflict). Datadog and Grafana both make JSON the interchange [Verified: docs.datadoghq.com configure; prior survey Grafana JSON model].

### 4.10 Keyboard and touch
- Every widget is a focusable `article` with `aria-label`; Tab order = `(y, x)` order; `Enter` on a focused widget opens its detail page (view) or its settings (edit).
- Chart keyboard: `←/→` pan, `+/−` zoom, `0` reset, `l` back to live — bound in `ChartToolbar`.
- Touch: drag needs a 250 ms long-press (dnd-grid's touch drag delay exists for the same reason: preserve page scroll [Verified: github mblode/dnd-grid]); RGL v2's `DragConfig.threshold` (3 px) plus a `touch-action: pan-y` on bodies; resize handles 24 px on coarse pointers (`@media (pointer: coarse)`). Command buttons ≥ 44 px on coarse pointers.
- Edit mode is desktop-first; on < 900 px, `Edit` is still offered but only the settings pane, add, remove and reorder (move up/down buttons) work — no drag.

---

## 5. Layout JSON

Versioned with `schema_version` from day one (Grafana `schemaVersion`; prior survey). Names are stable identifiers; titles are display text.

```jsonc
{
  "schema_version": 1,
  "name": "furnace",                 // unique per rig; URL-safe; the rig default is flagged server-side, not in the document
  "rig": "furnace-sim",              // GET /api/health .rig — informational; import warns on mismatch, does not refuse
  "title": "Furnace",
  "description": "Three zones, sample thermocouple, heaters",
  "readOnly": false,                 // hides loop/actuator controls even for editors (a wall display)
  "grid": { "cols": 24, "rowHeight": 24 },
  "defaults": { "windowS": 300, "every": 1, "yScale": "auto" },   // page-bar defaults; the bar overrides per session
  "widgets": [
    { "id": "w1", "kind": "health",   "x": 0,  "y": 0, "w": 24, "h": 2, "config": { "tiles": ["rig","recording","readers","loops","conditions","uptime"] } },
    { "id": "w2", "kind": "readout",  "x": 0,  "y": 2, "w": 6,  "h": 5, "bind": { "channel": { "source": "zone1", "measurand": "temperature" } }, "config": { "sparkline": true } },
    { "id": "w3", "kind": "chart",    "x": 6,  "y": 2, "w": 18, "h": 8, "title": "Zones °C",
      "bind": { "channels": [ { "source": "zone1", "measurand": "temperature" }, { "source": "zone2", "measurand": "temperature" } ],
                "overlays": [ { "loop": "heater1", "trace": "setpoint" } ] },
      "config": { "yScale": "range", "legend": "bottom", "legendValues": ["last"], "showBands": true, "syncGroup": "page" } },
    { "id": "w4", "kind": "loop",     "x": 0,  "y": 7, "w": 8,  "h": 8, "bind": { "loop": "heater1" }, "config": { "trends": true, "controls": true } },
    { "id": "w5", "kind": "markdown", "x": 16, "y": 10, "w": 8, "h": 4, "config": { "text": "**Cool-down**: stop heaters before opening the door." } }
  ]
}
```

JSON Schema (abridged but complete in structure; lives in `packages/client/src/dashboard.ts` as TypeScript types and in `controller/src/flyball/db/types.py` as frozen dataclasses so both ends validate the same thing):

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "flyball/dashboard/v1",
  "type": "object",
  "required": ["schema_version", "name", "grid", "widgets"],
  "properties": {
    "schema_version": { "const": 1 },
    "name": { "type": "string", "pattern": "^[a-z0-9][a-z0-9-_]{0,63}$" },
    "rig": { "type": "string" },
    "title": { "type": "string", "maxLength": 80 },
    "description": { "type": "string", "maxLength": 500 },
    "readOnly": { "type": "boolean", "default": false },
    "grid": { "type": "object", "properties": { "cols": { "enum": [12, 24], "default": 24 }, "rowHeight": { "type": "integer", "minimum": 16, "maximum": 64, "default": 24 } } },
    "defaults": { "type": "object", "properties": { "windowS": { "enum": [60, 300, 900, 3600] }, "every": { "enum": [1, 2, 5, 10, 20, 50] }, "yScale": { "$ref": "#/$defs/yScale" } } },
    "widgets": { "type": "array", "maxItems": 60, "items": { "$ref": "#/$defs/widget" } }
  },
  "$defs": {
    "channel": { "type": "object", "required": ["source", "measurand"], "properties": { "source": { "type": "string" }, "measurand": { "type": "string" } } },
    "yScale": { "oneOf": [ { "enum": ["auto", "range"] }, { "type": "object", "required": ["min", "max"], "properties": { "min": { "type": "number" }, "max": { "type": "number" } } } ] },
    "pos": { "type": "object", "required": ["x", "y", "w", "h"], "properties": { "x": { "type": "integer", "minimum": 0 }, "y": { "type": "integer", "minimum": 0 }, "w": { "type": "integer", "minimum": 1 }, "h": { "type": "integer", "minimum": 1 } } },
    "widget": {
      "allOf": [ { "$ref": "#/$defs/pos" } ],
      "type": "object",
      "required": ["id", "kind"],
      "properties": { "id": { "type": "string", "pattern": "^w[0-9a-z]{1,15}$" }, "title": { "type": "string", "maxLength": 80 } },
      "discriminator": { "propertyName": "kind" },
      "oneOf": [
        { "properties": { "kind": { "const": "readout" }, "bind": { "type": "object", "required": ["channel"], "properties": { "channel": { "$ref": "#/$defs/channel" } } },
          "config": { "type": "object", "properties": { "sparkline": { "type": "boolean", "default": true }, "sparklineS": { "type": "integer", "default": 300 }, "showRange": { "type": "boolean", "default": true }, "showSource": { "type": "boolean", "default": true }, "colorMode": { "enum": ["value", "none"], "default": "value" }, "decimals": { "type": "integer", "minimum": 0, "maximum": 6 }, "size": { "enum": ["auto", "s", "m", "l"], "default": "auto" } } } }, "required": ["bind"] },
        { "properties": { "kind": { "const": "gauge" }, "bind": { "oneOf": [ { "properties": { "channel": { "$ref": "#/$defs/channel" } }, "required": ["channel"] }, { "properties": { "loop": { "type": "string" } }, "required": ["loop"] } ] },
          "config": { "type": "object", "properties": { "kind": { "enum": ["auto", "dial", "arc", "bar", "thermometer", "tank"], "default": "auto" }, "showValue": { "type": "boolean", "default": true }, "showTicks": { "type": "boolean", "default": true }, "zones": { "enum": ["channel", "none"], "default": "channel" }, "neutral": { "type": "number" }, "setpoint": { "type": "boolean" } } } }, "required": ["bind"] },
        { "properties": { "kind": { "const": "chart" }, "bind": { "type": "object", "required": ["channels"], "properties": { "channels": { "type": "array", "minItems": 1, "maxItems": 8, "items": { "$ref": "#/$defs/channel" } }, "overlays": { "type": "array", "maxItems": 4, "items": { "type": "object", "required": ["loop", "trace"], "properties": { "loop": { "type": "string" }, "trace": { "enum": ["setpoint", "demand", "expected"] } } } } } },
          "config": { "type": "object", "properties": { "windowS": { "type": ["integer", "null"] }, "yScale": { "$ref": "#/$defs/yScale" }, "softMin": { "type": "number" }, "softMax": { "type": "number" }, "legend": { "enum": ["bottom", "right", "none"], "default": "bottom" }, "legendValues": { "type": "array", "items": { "enum": ["last", "min", "max", "mean"] }, "default": ["last"] }, "lineWidth": { "type": "number", "default": 1.5 }, "fill": { "type": "number", "minimum": 0, "maximum": 0.3, "default": 0 }, "points": { "enum": ["auto", "always", "never"], "default": "auto" }, "syncGroup": { "type": ["string", "null"], "default": "page" }, "showBands": { "type": "boolean", "default": true } } } }, "required": ["bind"] },
        { "properties": { "kind": { "const": "loop" }, "bind": { "type": "object", "required": ["loop"], "properties": { "loop": { "type": "string" } } },
          "config": { "type": "object", "properties": { "trends": { "type": "boolean", "default": true }, "controls": { "type": "boolean", "default": true }, "showLaw": { "type": "boolean", "default": false }, "showFeedforward": { "type": "boolean", "default": false } } } }, "required": ["bind"] },
        { "properties": { "kind": { "const": "actuator" }, "bind": { "type": "object", "required": ["actuator"], "properties": { "actuator": { "type": "string" } } },
          "config": { "type": "object", "properties": { "fields": { "oneOf": [ { "const": "all" }, { "type": "array", "items": { "type": "string" } } ] }, "commands": { "type": "array", "items": { "type": "string" }, "default": [] }, "compact": { "type": "boolean", "default": true } } } }, "required": ["bind"] },
        { "properties": { "kind": { "const": "program" }, "bind": { "type": "object", "properties": { "program": { "type": "string" } } },
          "config": { "type": "object", "properties": { "showSteps": { "type": "boolean", "default": true }, "controls": { "type": "boolean", "default": true }, "programs": { "type": "array", "items": { "type": "string" } } } } } },
        { "properties": { "kind": { "const": "events" }, "bind": { "type": "object", "properties": { "scope": { "type": "string" }, "subject": { "type": "string" } } },
          "config": { "type": "object", "properties": { "levels": { "type": "array", "items": { "enum": ["DEBUG", "INFO", "WARNING", "ERROR"] }, "default": ["WARNING", "ERROR"] }, "limit": { "type": "integer", "minimum": 5, "maximum": 500, "default": 50 }, "columns": { "type": "array", "items": { "enum": ["time", "level", "scope", "kind", "message"] } }, "filter": { "type": "string" } } } } },
        { "properties": { "kind": { "const": "conditions" }, "config": { "type": "object", "properties": { "minLevel": { "enum": [10, 20, 30, 40], "default": 30 }, "groupBy": { "enum": ["device", "none"], "default": "none" } } } } },
        { "properties": { "kind": { "const": "health" }, "config": { "type": "object", "properties": { "tiles": { "type": "array", "items": { "enum": ["rig", "recording", "readers", "loops", "conditions", "signals", "events", "uptime"] } }, "layout": { "enum": ["row", "grid"], "default": "row" } } } } },
        { "properties": { "kind": { "const": "recording" }, "config": { "type": "object", "properties": { "showLast": { "type": "integer", "default": 3 }, "confirmStop": { "type": "boolean", "default": true } } } } },
        { "properties": { "kind": { "const": "markdown" }, "config": { "type": "object", "required": ["text"], "properties": { "text": { "type": "string", "maxLength": 20000 }, "align": { "enum": ["left", "center"], "default": "left" } } } }, "required": ["config"] },
        { "properties": { "kind": { "const": "heading" }, "config": { "type": "object", "properties": { "text": { "type": "string" }, "spacer": { "type": "boolean", "default": false }, "collapsible": { "type": "boolean", "default": false } } } } },
        { "properties": { "kind": { "const": "link" }, "config": { "type": "object", "required": ["label", "target"], "properties": { "label": { "type": "string" }, "icon": { "type": "string" }, "variant": { "enum": ["link", "button", "danger"], "default": "link" }, "confirm": { "type": "string" },
            "target": { "oneOf": [ { "type": "object", "required": ["page"], "properties": { "page": { "type": "string" }, "name": { "type": "string" } } }, { "type": "object", "required": ["dashboard"], "properties": { "dashboard": { "type": "string" } } }, { "type": "object", "required": ["command"], "properties": { "command": { "type": "object", "required": ["actuator", "tag"], "properties": { "actuator": { "type": "string" }, "tag": { "type": "string" }, "args": { "type": "object" } } } } } ] } } } }, "required": ["config"] },
        { "properties": { "kind": { "const": "table" }, "bind": { "type": "object", "properties": { "channels": { "type": "array", "items": { "$ref": "#/$defs/channel" } }, "sources": { "type": "array", "items": { "type": "string" } }, "all": { "type": "boolean" } } },
          "config": { "type": "object", "properties": { "columns": { "type": "array", "items": { "enum": ["channel", "value", "unit", "bar", "sparkline", "status", "updated"] } }, "sortBy": { "enum": ["source", "name", "status"], "default": "source" }, "groupBySource": { "type": "boolean", "default": true }, "sparkline": { "type": "boolean", "default": false } } } } },
        { "properties": { "kind": { "const": "image" }, "config": { "type": "object", "required": ["src"], "properties": { "src": { "type": "string", "maxLength": 2000000 }, "fit": { "enum": ["contain", "cover"], "default": "contain" }, "pins": { "type": "array", "maxItems": 24, "items": { "type": "object", "required": ["x", "y", "channel"], "properties": { "x": { "type": "number" }, "y": { "type": "number" }, "channel": { "$ref": "#/$defs/channel" } } } } } } }, "required": ["config"] }
      ]
    }
  }
}
```

Server side (mirrors `program`/`tuning`): table `dashboard(id, name, rig, body JSON, schema_version, created_ns, notes)` append-only per name; routes `GET /api/dashboards` (newest per name + which is default), `GET /api/dashboards/{name}` (body + `problems[]`), `PUT /api/dashboards/{name}` (201, new version; validates against the schema and the live rig registry, returns `problems[]`), `GET /api/dashboards/{name}/history`, `POST /api/dashboards/{name}/rename`, `POST /api/dashboards/{name}/default`, `DELETE /api/dashboards/{name}` (409 if default), `GET /api/dashboards/{name}/download` (attachment). `GET /api/dashboards/overview` is **generated** from the schema when no row named `overview` exists (health strip; one chart per unit; readouts per channel 6 wide; one loop faceplate per loop 8 wide; actuator cards 6 wide) — the HA "auto-generated until you take control" rule. Import of bundled presets: same `import_directory` mechanism `routes/library.py` uses, from `examples/<rig>/dashboards/*.json`.

Migration: `schema_version` bump = a function in `packages/client/src/dashboard.ts` `migrate(doc): Dashboard` chained per version; the server stores what it was given and migrates on read.

---

## 6. Performance budget

Numbers are targets on a Raspberry Pi 4-class client browser and a laptop; measure with Chrome's performance panel and `PerformanceObserver('longtask')`. uPlot's own figures — 3,600 points streamed at 60 fps costs ~10 % CPU and 12 MB [Verified: uPlot README] — are the per-chart ceiling to stay far under.

| Rule | Number | Why / source |
|---|---|---|
| One websocket per stream, one fan-out | 5 sockets total (`samples`, `loops`, `actuators`, `events`, `readers` — the last reports every read and was being opened once per widget); `signals` stays polled | today each `useStream` call opens its own socket; N widgets = N sockets |
| Telemetry store, not React state | samples land in a `TelemetryStore` (ring buffers: `Float64Array` t/v per channel, capacity `3600 s × 10 Hz = 36 000` ≈ 576 KB per channel; 40 channels ≈ 23 MB — acceptable; make capacity `windowMax × rate` from `period_s`) | `useSamples` today copies whole arrays per sample (`[...trace.t.slice(start), time]`) and re-renders the app |
| Widget subscription granularity | `useLatest(channel)` re-renders only that widget, at most 4 Hz (batched in one `requestAnimationFrame` per store tick); charts subscribe by ref and never re-render React | `useSyncExternalStore` with a per-channel version counter |
| `setData` cadence | ≤ 10 Hz per chart (coalesce: one rAF flush per animation frame, each chart at most every 100 ms; sparklines every 500 ms) | uPlot `setData(data, resetScales)` redraws synchronously [Verified: uPlot d.ts]; `batch()` groups several ops |
| Live charts per view | ≤ 12 full charts + ≤ 24 sparklines *mounted*; above that the editor warns "heavy dashboard" | each uPlot instance is a canvas + legend DOM; 12 × 10 Hz `setData` at ≤ 2 ms each ≈ 24 % of a 60 fps budget on a Pi [Inference from uPlot numbers] |
| Off-screen pause | `IntersectionObserver` with `rootMargin: "200px"`, threshold 0; a hidden chart unsubscribes and gets one `setData` on re-entry; a hidden readout still updates its text (cheap) | MDN: IO offloads visibility to the browser instead of scroll handlers [Verified: MDN IO]; cancelling the update loop per hidden canvas was the biggest single win in a shipped case [Verified: freecodecamp / dinimiciuil via search summary] |
| Tab hidden | `document.visibilityState === "hidden"` → stop all chart updates; keep the store filling | free |
| Points per series per chart | ≤ `2 × plot width px`, min 300, max 4 000; thin with `every = ceil(n / cap)` (keep `thin()`), or min/max per bucket for > 4 000 | Foxglove min/max/first/last downsampling keeps extremes [Verified: Foxglove plot] |
| History seed | one `/series` request per channel per session with `max_points = 2 × width`, in parallel, at most 20 sessions walked (as now) | already |
| Memo boundaries | `PanelFrame`, every widget body, `ChartToolbar`, `LoopControls`, all `React.memo`; props are primitives + stable callbacks; the dashboard `widgets[]` array identity changes only on edit | `LoopControls` already documents why |
| Edit mode | RGL mounted only in edit mode; `onLayoutChange` applied on `onDragStop`/`onResizeStop` only; the widget bodies are `pointer-events: none` and charts frozen (no `setData`) while dragging | RGL v2 benchmarks: 100-item drag step 3.7 ms [Verified: RGL v2 RFC] |
| Layout thrash | chart hosts keep `contain: inline-size` (already); one `ResizeObserver` per chart (already); gauges are SVG with `viewBox`, resized by CSS only | |
| Bundle | dashboard engine + RGL ≤ 60 KB gzipped on top of today's bundle; measure with `vite build --report` | RGL 2.2.4 is 447 KB unpacked with react-draggable/react-resizable deps [Verified: npm registry] — tree-shake `react-grid-layout/react` + `core` only |
| Long tasks | 0 long tasks (> 50 ms) in steady state; first dashboard paint < 1.5 s on a Pi after schema load | measured, not assumed |

Websocket back-pressure: the server already drops oldest samples if a client lags (`/ws/samples`) [Verified: api.md]; the client should show the `● live` chip amber when it observes a `seq` gap.

### 6.1 Grid library — recommendation

| Library | Version | Size | Fit |
|---|---|---|---|
| **react-grid-layout** | 2.2.4, React ≥ 16.3 peer, TypeScript rewrite (v2 Dec 2025), hooks `useContainerWidth`/`useGridLayout`, `core` module of pure layout functions, pluggable compactors, constraints (`minW/maxW/minH/maxH`), `transformStrategy` [Verified: github RGL README, RFC, npm registry] | 447 KB unpacked; deps react-draggable, react-resizable, resize-observer-polyfill | **Recommended.** Mount only in edit mode; use `core` (`moveElement`, `compact`) for keyboard moves and for the "first free slot" on add without mounting the component |
| gridstack.js | 13.3.0 (README says v14), no deps, official React wrapper renders widgets via portals; caveats: `useGridStack()` inside `<GridStack>`, React children render after the grid DOM, custom handles need `refreshDragHandles` [Verified: gridstack README / react README / npm] | 2.1 MB unpacked | No: the DOM is owned by gridstack, React by portals; two owners of the same tree is the class of bug we do not want beside uPlot's own DOM |
| @dnd-grid/react | 1.3.1, based on RGL with weighted drag physics, edge auto-scroll, touch delay, aria-live; 14 stars, 184 commits [Verified: github, npm] | 241 KB | No: single-maintainer, tiny community; its good ideas (touch delay, auto-scroll) are re-implementable on RGL |
| dnd-kit + CSS grid | drag only; no resize, no compaction — you write the layout engine | small | No: writing collision + compaction is what RGL's `core` already is |
| muuri | 0.9.5, drag/sort/filter, no resize [Verified: npm] | 1 MB | No |
| tldraw | an infinite-canvas drawing SDK, not a grid | large | No |

RGL usage rules: `cols=24`, `rowHeight=24`, `margin=[12,12]`, `compactType="vertical"`, `isBounded`, `draggableHandle=".fb-widget-handle"`, `resizeHandles=["se"]` (`["se","e","s"]` on coarse pointers), `useCSSTransforms` (default strategy), `preventCollision=false`, `allowOverlap=false`; `onLayoutChange` ignored, `onDragStop`/`onResizeStop` commit to the document.

---

## 7. Implementation plan

Two agents can work in parallel: **A** owns `packages/react/src/{store,dashboard}`, `packages/client/src/dashboard.ts`, the app's `pages/Dashboards.tsx`, and the backend `dashboard` table/routes; **B** owns `styles.css`, `theme.tsx`, `app.css`, `PanelFrame`, and the existing panels/pages. The seam: B lands the token set and `PanelFrame` first (A-1 can start before that using the legacy aliases); A lands the store before touching pages B is polishing.

### Agent A — dashboard engine and persistence

1. **Telemetry store.** `packages/react/src/store/telemetry.ts`: `TelemetryStore` with ring buffers per channel (`Float64Array`, doubling on overflow up to cap), `latest(channel)`, `slice(channel, fromS)`, `subscribeLatest(channel, cb)`, `subscribeTrace(channels, cb)` batched per rAF; `RigProvider` creates one and opens the 4 sockets lazily (first subscriber opens; last closes after 5 s). Hooks: `useLatest(channel)`, `useTraceRef(channels)` (returns a ref + a `version` for charts), `useLoopLatest(name)`, `useActuatorState(name)`, `useEventsFeed(filter, limit)`. Keep `useSamples`/`useLoops` as thin adapters over the store so nothing else breaks.
   Files: new `store/*.ts`; edit `provider.tsx`, `hooks/useSources.ts`, `hooks/useLoops.ts`, `hooks/useStream.ts`, `index.ts`.
   Accept: with the sim rig at ×60 and 4 sources, the React profiler shows `App` not re-rendering on samples; `Overview` re-renders ≤ 4 Hz; heap stable over 30 min (Chrome memory panel, ±5 %).
2. **Chart subscription mode.** `MultiSeries`/`TimeSeries`: new prop `source: TraceRef` alternative to `series/t/v`; the component subscribes with `store.subscribeTrace` and calls `setData` inside the store's rAF flush; `IntersectionObserver` + `visibilitychange` pause; `cursor.sync.key` prop; `showBands`; legend values. Sparkline cadence 500 ms.
   Files: `panels/MultiSeries.tsx`, `panels/TimeSeries.tsx`, new `panels/useChartLifecycle.ts`.
   Accept: a page with 12 charts scrolled so 6 are off-screen shows `setData` calls only for the visible 6 (count via a debug counter on `window.__fb.charts`); no React re-render of the chart component during streaming (profiler).
3. **Dashboard types + schema.** `packages/client/src/dashboard.ts`: TS types for §5, `DASHBOARD_SCHEMA` (JSON Schema), `migrate()`, `validateAgainstRig(doc, schema, sources, loops)` → `problems[]`, `generateOverview(schema, sources, loops)`. Python mirror: `flyball/runtime/dashboard.py` frozen dataclasses + the same validation used by the route.
   Accept: round trip `generateOverview → JSON → parse → validate` has zero problems on the furnace sim; a doc naming `zone9` yields exactly one problem naming the widget id.
4. **Backend persistence.** `db/sqlite.py` table `dashboard` (copy the `program` table pattern), `db/store.py` methods, `server/routes/dashboards.py` with the routes in §5, mounted in `routes/__init__.py`; `import_directory` for `examples/*/dashboards`; docs in `book/src/6-reference/api.md`; tests `controller/tests/test_dashboards.py` (save/version/history/rename/default/delete-refuses-default/problems).
   Accept: `pytest controller/tests/test_dashboards.py` green; `GET /api/dashboards/overview` on a fresh DB returns the generated doc with `generated: true`.
5. **Client + hooks.** `RigClient.dashboards()/dashboard(name)/saveDashboard/…` in `packages/client/src/rig.ts`; `useDashboards()`, `useDashboard(name)` in `packages/react/src/hooks/useDashboards.ts`.
   Accept: typecheck passes; a fake transport test saves and lists.
6. **Widget registry + renderer.** `packages/react/src/dashboard/registry.ts`: `{kind → {component, configSchema, defaultSize, minSize, accepts(binding), preview}}`; `DashboardView` renders CSS grid (view mode) from a doc; `WidgetHost` wraps each in `PanelFrame` with the unbound/stale/error states. Widgets in order: `readout`, `chart`, `heading`, `markdown`, `health`, `conditions`, `gauge`, `loop`, `actuator`, `events`, `link`, `table`, `program`, `recording`, `image` — each is mostly an existing panel plus a binding resolver.
   Accept: the generated overview renders at 2560 px with no horizontal scroll and matches the widget default sizes; below 900 px it stacks in `(y,x)` order.
7. **Editor.** `DashboardEditor`: RGL (edit only), page-bar edit state, add-widget dialog (By thing / By widget with live previews from the store), settings pane (`SchemaForm` on `configSchema` + binding picker `RigTreePicker`), duplicate/remove, keyboard moves via `react-grid-layout/core`, undo/redo stack, sessionStorage draft, Save ▾ menu (Save / Save as / Rename / Set default / Delete / Export / Import), unbound rebind flow.
   Files: `packages/react/src/dashboard/{DashboardEditor,AddWidgetDialog,WidgetSettings,RigTreePicker,history}.tsx`; app `pages/Dashboards.tsx`, `router.ts` (`#/dashboards/<name>`), `Shell.tsx` switcher.
   Accept: scripted Playwright run: add a readout by thing, resize it, undo, redo, save as "test", reload → same layout; rename a source in the sim rig file and reload → the widget shows the unbound state and Rebind resolves it; RGL is absent from the DOM in view mode (`document.querySelector('.react-grid-layout') === null`).
8. **Presets and docs.** `examples/simulated/dashboards/overview.json` + `furnace.json`; `book/src/3-running/dashboards.md` (what a dashboard is, the JSON, import/export); `UI.md` §4 updated to point here.
   Accept: `mdbook build` passes; fresh sim start shows the "Furnace" dashboard in the switcher.

### Agent B — visual polish of existing pages

1. **Tokens.** Replace the three `:root` blocks in `styles.css` with §1.1 (light base, dark under both scopes, legacy aliases). `theme.tsx`: `makeTheme` reads tokens; `AppTheme` sets `data-theme`; `spacing: 4`; remove `FbVars` (or reduce it to writing nothing). `app.css`: `--gutter` = `var(--fb-space-4)` etc.
   Accept: both modes screenshot at 1440 and 2560; every text/background pair in the app ≥ 4.5:1 (axe DevTools or `contrast.mjs` on the token list); charts pick up the new series colours (the `--fb-series-*` read in `MultiSeries` needs no change).
2. **PanelFrame + status dot + severity borders.** New `packages/react/src/panels/PanelFrame.tsx` (title row, unit/subtitle, status dot, actions slot, footer, `severity: "ok"|"warn"|"alarm"|"stale"`); migrate `fb-panel`, `Readout`, `UnitCharts` sections, `LoopPanel`, `ActuatorPanel`, `EventsPanel`, and the app's `DeviceCard`/`ReaderCard` to it. Replace `ok`/`running` chips with the dot; keep chips for mode text.
   Accept: on the Overview with the healthy sim, the only saturated colours on screen are chart series and the REC dot; a `SimActuator` fault (Simulation page) turns exactly one card's border and dot.
3. **Stale detection.** `alarmLevel` gains `"stale"`; `Readout`/`Gauge`/table rows compute it from `period_s` (health/readers) and last sample time; dashed border + "last sample 42 s ago" footer.
   Accept: stop a reader in the sim; within 3 periods its tiles go dashed and the alarm chip count is unchanged (stale is not an alarm).
4. **Typography pass.** Apply the §1.3 scale: readout value weight 500, unit at .5em baseline-aligned, labels 11 px uppercase `--fb-fg-2`, panel titles 14/600, `--fb-text-*` variables used instead of `0.85em`/`0.9em` literals in `styles.css` (there are 14 such literals).
   Accept: `grep -c "em;" styles.css` for font sizes drops to 0 outside the readout clamp; digits do not shift width under noise (record a 10 s screen capture of a readout; the unit x-position is constant).
5. **App bar + status chips.** `Status.tsx`: one alarm-summary chip always present (count, colour by max level), one `● live` stream chip, REC chip with red dot, program chip, sim chip; healthy states `color="default"`. `Shell.tsx`: dashboard switcher slot (rendered by A-7; B leaves a `startSlot` prop).
   Accept: healthy app bar contains no green; alarm chip reads `⚠ 0` grey.
6. **Loop faceplate layout.** `LoopPanel.tsx`/`styles.css`: PV/SP/OP rows with bars and SP notch, deviation, banner for the top condition (PlantPAx), trends 100 px stacked at the right, law/feedforward behind a `details` (open on the L3 page only via prop `detail`). Controls move into the SP row.
   Accept: the Loops page at 2560 shows 3 faceplates abreast with no empty column (screenshot vs current `after-loops-2560.png`); PV/SP/OP labels present; keyboard: Tab reaches setpoint field → Move → Stop in that order.
7. **Section heads, empty states, density.** `SectionHead` and `StateBlock` in `cards.tsx`, used on every page; density toggle (`comfortable`/`compact`) beside the theme toggle, stored as `flyball.density`, sets `data-density` on `<html>` and the CSS in §2.
   Accept: every list page with an empty sim rig shows a `StateBlock` with an action; toggling density changes `--fb-gap` and tile heights only (no reflow of the sidebar).
8. **Focus and motion.** Global `:focus-visible` ring token on MUI (`theme.components.MuiButtonBase.styleOverrides` → `&.Mui-focusVisible { boxShadow: var(--fb-focus) }`) and on library controls; transitions use `--fb-dur-*`; one-shot 600 ms border pulse on entering warn/alarm (`@keyframes fb-pulse`, guarded by reduced-motion).
   Accept: Tab through the Overview with the keyboard: every stop has a visible ring; with OS reduced-motion on, `getComputedStyle(x).transitionDuration === "0s"` for a hovered card.
9. **Chart chrome.** `ChartToolbar` keyboard bindings (`←/→/+/−/0/l`); legend shows last value; grid `--fb-grid`; axis stroke `--fb-axis`; line width 1.5 (2 for a single-series chart in light mode); setpoint overlays dashed `--fb-series-setpoint`.
   Accept: hovering one unit chart on the Overview moves the cursor on the others (sync key = page); the legend of a 4-series chart lists `zone1 654.7` etc.
10. **Screenshots + docs.** Re-take `after-overview-2560.png`/`after-loops-2560.png` and phone-width shots; update `ui/README.md` "Theming" with the token table.
    Accept: the four screenshots attached to the PR; README lists every `--fb-*` token with its purpose.

---

## 8. Decisions (were open; taken 16 Sep 2026)

1. Grid columns: **24**. `grid.cols` may still be 12 in a stored document (schema enum); the client scales x/w ×2 on read and generates at 24.
2. Font: **`system-ui`**. Revisit only if a Pi screenshot shows digit-width problems.
3. `readOnly` dashboards: hide loop/actuator controls only. The page-bar chart controls stay (kiosk mode is a later, separate flag).
4. `recording` widget ships **read-only** (state + link to Sessions) until the recording start/stop routes (UI.md B9) exist.

---

## 9. Sources read in this session

Grafana: https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/gauge/ · https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/time-series/ · https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/stat/ · https://grafana.com/docs/grafana/latest/dashboards/use-dashboards/ · https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/ · https://raw.githubusercontent.com/grafana/grafana/main/packages/grafana-data/src/themes/createColors.ts · https://raw.githubusercontent.com/grafana/grafana/main/packages/grafana-data/src/themes/palette.ts · https://raw.githubusercontent.com/grafana/grafana/main/packages/grafana-data/src/themes/createVisualizationColors.ts · library panels (search summary only): https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/manage-library-panels/ [Unverified]. Edit-dashboards pages 404'd (two URL variants) — edit-pane details marked [Unverified].

Ignition: https://www.docs.inductiveautomation.com/docs/8.1/appendix/components/perspective-components/perspective-display-palette/perspective-alarm-status-table · style classes / themes via search summary only: https://www.docs.inductiveautomation.com/docs/8.1/ignition-modules/perspective/styles/perspective-built-in-themes , https://www.docs.inductiveautomation.com/docs/8.1/ignition-modules/perspective/styles/style-classes/how-to-change-style-on-hover [Unverified]. Symbols pages 404'd.

Phoebus: https://raw.githubusercontent.com/ControlSystemStudio/phoebus/master/app/display/Readme.md · https://control-system-studio.readthedocs.io/en/latest/app/display/editor/doc/index.html

ISA-101 / HPHMI: https://plcprogramming.io/blog/high-performance-hmi-isa-101 · (search summaries) https://edwartens.co.uk/blog/hmi-design-best-practices-ISA-101-guide , https://industrialmonitordirect.com/blogs/knowledgebase/applying-high-performance-hmi-handbook-concepts-to-scada-design

Rockwell PlantPAx: https://literature.rockwellautomation.com/idc/groups/literature/documents/rm/proces-rm200_-en-p.pdf (text-extracted with pdftotext; faceplate alarm banner, linear gauge alarm colours, tab border colour, severity colour table)

Foxglove: https://docs.foxglove.dev/docs/visualization/panels · https://docs.foxglove.dev/docs/visualization/panels/plot · https://docs.foxglove.dev/docs/visualization/layouts

Home Assistant: https://www.home-assistant.io/dashboards/sections/ · https://www.home-assistant.io/dashboards/tile/ · https://www.home-assistant.io/blog/2026/06/03/release-20266/

Bluesky: https://blueskyproject.io/bluesky-queueserver/ · https://blueskyproject.io/bluesky-queueserver/introduction_for_users.html · https://blueskyproject.io/bluesky-widgets/ · https://github.com/cnpem/sophys-gui

Datadog / Kibana / Chronograf / Node-RED: https://docs.datadoghq.com/dashboards/configure/ · https://docs.datadoghq.com/dashboards/widgets/ · https://www.elastic.co/docs/explore-analyze/dashboards/create-dashboard · https://docs.influxdata.com/chronograf/v1/guides/visualization-types/ · https://dashboard.flowfuse.com/nodes/widgets.html · https://dashboard.flowfuse.com/nodes/widgets/ui-gauge.html

Instruments: https://www.tek.com/en/blog/inside-5-series-mso-re-inventing-oscilloscope-user-interface · https://www.electronicdesign.com/home/article/21200777/navigating-oscilloscope-user-interfaces · Keysight and NI VeriStand: search summaries only [Unverified]. WinCC Unified faceplate guidance: search summary only [Unverified].

Dark UI: https://m2.material.io/design/color/dark-theme.html · Apple HIG pages returned no body (JS-rendered) [Unverified]. Material 3 colour roles page likewise [Unverified].

Colour: https://sronpersonalpages.nl/~pault/ · Okabe-Ito hex via search summaries (scifig.ai, conceptviz.app) · validator: bundled dataviz skill `scripts/validate_palette.js` and `references/palette.md` (runs recorded above).

Grid / charts / platform: https://github.com/react-grid-layout/react-grid-layout · https://raw.githubusercontent.com/react-grid-layout/react-grid-layout/master/rfcs/0001-v2-typescript-rewrite.md · https://registry.npmjs.org/react-grid-layout/latest · https://github.com/gridstack/gridstack.js/blob/master/README.md · https://raw.githubusercontent.com/gridstack/gridstack.js/master/react/README.md · https://registry.npmjs.org/gridstack/latest · https://github.com/mblode/dnd-grid · https://registry.npmjs.org/@dnd-grid/react/latest · https://registry.npmjs.org/muuri/latest · https://raw.githubusercontent.com/leeoniya/uPlot/master/README.md · https://raw.githubusercontent.com/leeoniya/uPlot/master/dist/uPlot.d.ts · https://developer.mozilla.org/en-US/docs/Web/API/Intersection_Observer_API · motion/typography via search summaries: https://www.designsystems.one/foundations/duration-and-easing , https://rsms.me/inter/ , https://www.freecodecamp.org/news/high-frequency-real-time-data-in-react-from-ring-buffers-to-offscreencanvas/ [Unverified beyond the summaries].

---

## 10. Implementation log

Kept as work lands, newest last. Each entry: what was built against this spec, what deviated and why. Entries reference the section they change.

- **16 Sep 2026 — start of the second pass.** Tree committed (`fc59e2e`). Baseline: 259 backend tests, typecheck/build green; `#/dashboards` logs 8 × "Maximum update depth exceeded" in 6 s on the furnace rig. Six parallel agents started: stress rigs as TOML (`examples/stress/`), dashboards engine fix + reconcile (§4–§6), tokens/theme/app-bar chips (§1, §2), backend (rate feedforward, `output_range`, test order bug), humanised text (event kinds, subjects, state keys, device tags), `yaml`+`smol-toml` parsers. Telemetry store (§6, A-1/A-2), `PanelFrame` (B-2), stale detection (B-3) and the PV/SP/OP faceplate (B-6) follow once the tokens land, because they all touch `styles.css` and the panels.
- **16 Sep 2026 — phase 1 landed.** Backend 287 tests, ruff clean; UI typecheck/build clean (bundle 438.8 kB gz, +31 kB of which is `yaml`+`smol-toml`).
  - §1.1 tokens are in `styles.css` verbatim; `theme.tsx` reads them (`token()`), `FbVars` gone, MUI `spacing: 4`, every `sx` rescaled ×1.5. Finding: `--fb-ok/--fb-warn/--fb-stale` reach only 3.96–4.24:1 against `bg-0`/`bg-2` in light mode — the contrast figures in §1.1 hold against `bg-1` only. MUI slots: `h1` = page, `subtitle2` = title, `h2`/`overline` = label.
  - §2 app bar: alarm chip (30–39 amber, ≥ 40 red — the amber band was unspecified), one `● live` chip, REC dot, program/sim chips; Dashboards first in the sidebar, Overview kept second; `Shell.startSlot` carries the dashboard switcher. Density toggle stored as `flyball.density`; the chart-height floor (`--fb-chart-min-h`) is declared but not yet read by `MultiSeries`/`TimeSeries`.
  - §4/§5/§6 dashboards: 24 columns (12-col documents scaled ×2 on read); view mode is a plain CSS grid, RGL only in edit mode (asserted by `dash-e2e.mjs`); server returns `problems[]`; presets `examples/simulated/dashboards/{overview,furnace}.json` imported on start; off-screen charts get 0 redraws (measured). The "Maximum update depth exceeded" loop was **not** in the dashboard code: `useStream` did one `setState` per websocket message, and at 60× the furnace exceeded React's nested-update limit; it now coalesces per animation frame. `history.ts` mutated refs inside a `setState` updater (Strict Mode runs updaters twice) — fixed.
  - **Outstanding vs this spec:** §4.3 "By thing" picker (only "By widget" exists); §4.5 keyboard move/resize; §4.7 server-side rig default (home is `localStorage` only) and generated `/api/dashboards/overview` (client-side `generateOverview` instead); §4.9 import lands as a draft, Save is a separate step; document has no `title`/`readOnly`/`defaults`.
  - §3.4 loop faceplate in a widget is unusable at 8×8 today (title truncated, trends collapsed to no plot area) — next pass.
  - Text: event kinds, subjects, law-state keys, device tags and sim parameters are humanised through `describeEventKind/describeSubject/describeStateKey/describeDevice/describeSimParam` in `packages/client/src/schema.ts`. `SourceDeclaration.label` is on the wire.
  - Control: rate feedforward (`rate_gain` on `affine`/`table`, the ramp supplies dSP/dt); furnace zone 2 overshoot on a 15 °C/min ramp: plain PI 4.0 °C, static table 7.6 °C, table + rate 2.2 °C. `output_range` on `ActuatorState` (sim actuators; SCPI/Modbus configs still need the field).
  - Stress rigs (`examples/stress/`, all TOML): `plant` (41 channels, 20 units, 17 loops), `torrent` (~11 600 samples/s measured), `zoo`, `sparse`, `bare`, `chaos`, `longrun`; ordinary `chiller`/`dual` in `examples/simulated`. What they showed: the alarm chip counts device conditions only — 18 amber tiles on `plant` and the chip reads 0 (§2 must count channel band excursions); `Measurand` is interned by name with range/bands attached, so one rig cannot carry two `temperature` channels with different ranges or units (design question, see the handoff).
- **16 Sep 2026 — phase 2 landed.** Backend 295 tests; UI typecheck/build clean (446.7 kB gz), 14 vitest tests for the store; `dash-e2e.mjs` ALL PASS; every furnace page 0 console errors / 0 warnings.
  - §6 telemetry store: `packages/react/src/store/{ring,telemetry,hooks,debug}.ts`, `panels/useChartLifecycle.ts`; `useSamples`/`useLoops`/`useActuatorStates`/`useEvents` are thin adapters at ≤ 4 Hz; `App.tsx` holds no live hook; charts take a `source` trace ref and `setData` inside the store's rAF flush, paused off-screen and on hidden tabs; `useQuery` skips identical poll results. Measured (dev build, Strict Mode, Ryzen 5950X, `$S/tools/perf.mjs`): furnace `#/` 30 s — TaskDuration 25.8 s → 2.4 s, `App` renders 2350 → 0; plant `#/dashboards` 10 s — 9.2 s → 3.9 s, long tasks 53 → 0; torrent `#/` 60 s — 45.9 s → 11.6 s, long tasks 158 → 0, heap +5.8 % over 60 s with forced GC. 12-chart page with 6 off-screen: the hidden six get 0 `setData`. Points per chart are capped per *chart* (split across traces, min 300 each) because `uPlot.join` otherwise builds an 8×2400 joined axis. Next lever: the daemon sends one websocket message per sample; on torrent ~25 % of a core is browser message dispatch before any JS runs — batch per frame server-side.
  - §2/§3.1 `PanelFrame` (title row, status dot, severity borders, one-shot pulse) under `Readout`, `Gauge`, `UnitCharts`, `ActuatorPanel`, `EventsPanel`, `Tile`/`WidgetFrame`; `SectionHead`/`StateBlock` on every list page; stale = no sample for > max(3 × `period_s`, 5 s) in rig time (dashed border, hollow dot, "last sample N s ago"). `DeviceCard`/`ReaderCard` keep MUI chrome with a `StatusDot` rather than `PanelFrame` (they are MUI `sx` cards). The alarm summary is server-side: `GET /api/health` `alarms {warn, alarm, max_level}` = channels outside warn/alarm bands + device conditions ≥ 30; on `plant.toml` the chip reads 19 (18 amber + 1 red) as research §6 demanded.
  - §3.4 faceplate: PV / SP / OP rows with bars (PV bar carries the warn/alarm ticks and an SP notch; OP bar is % of `output_range`), deviation, one-condition banner, `AT LIMIT` marker with "requested N W" caption while clamped, law/feedforward behind `<details>` on the L3 page only; setpoint/achievable overlays are `--fb-series-setpoint` dashed. Widget 8×8 measured: 339–513 px × 110 px plot per trend. Gaps: `LoopOut` has no ramp target/rate (caption reads "following linear ramp setpoint"); `mode: "open"` is never emitted — the UI keys on `law.tag === "open_loop"`.
  - Chart keyboard: `←/→` pan, `+/−` zoom, `0` fit, `l` live on `ChartToolbar`; `cursor.sync` by page hash.
  - Backend: `output_range` on SCPI/Modbus actuators (plus a bug where their states never passed it); recorder stores actuator config and source labels (migration `0006_labels.sql`); programs gained `hold: {timeout}` and a `command` step (`device_command:` — `command` is reserved for the step tag on the wire), so `chaos-run.yaml` now fails/restores a reader and disturbs an actuator from config alone; `GET /api/sim` has `device: bool` so the UI stops probing `/api/sim/device` on rigs without one.
- **16 Sep 2026 — phase 3 (sweep and fixes).** Every page of all 13 rigs (6 simulated, 7 stress, humidity) at 1440 light/dark and 400 px: 0 console errors, 0 warnings. Fixed from the sweep: events' relative time under a fast clock (store `nowS()`), duplicate widget ids in `generate.ts` when unit names sanitise alike, Actuators page shows the clamped output with `AT LIMIT` and the raw request as a caption (chaos zone 2 requested 22.8 MW against 0–1000 W), `#/dashboards` keeps the app-bar chips at 400 px (switcher goes icon-only below 600 px), Programs page/widget/app-bar chip show the new **failed** program state, `readerOffline` banner wired from freshness, `--fb-chart-min-h` honoured by the charts, mini trends banded around SP instead of auto-fitting to noise. Backend: a program step that raises now ends the run *failed* (ERROR events `step_failed` + `failed`, `failed`/`error` on `/api/programs/running`) — previously it logged "finished". Tooling kept in `scripts/ui-check/`. Still open: §10 "outstanding vs this spec" (By-thing picker, keyboard widget moves, server rig default, import auto-save, `readOnly`), the Overview tile wall for ≥ 12 same-unit sources, websocket batching, and the `Measurand`-vs-`Channel` bands question in `UI_HANDOFF.md` §3.2.

- **16 Sep 2026 — one frame per widget (§2 chrome, §3 sizes).** The user's 2341×1165 dark screenshot showed every widget as a card inside a card (`PanelFrame` inside `WidgetFrame`'s `Tile`), row heights that did not hold their content (8×8 faceplates clipping their trends, 6×5 actuator cards scrolling a description, sections and forms), and an overlay whose only close was a faint ×. Rules now in force:
  - **The one-frame rule.** `WidgetFrame` (apps/dashboard) renders the only `PanelFrame` a widget has — 28px title row (status dot · title · subtitle · text status · actions: grip and `⋯` in edit mode), body padded `--fb-space-3`, optional 12px footer — and every widget renders its **body only**. Library panels with their own chrome for the L3 pages (`Readout`, `LoopPanel`, `ActuatorPanel`) take `bare` to drop it; a widget hands the frame what only the body knows (severity, severity label, footer, a `Ref` title, a subtitle, a mode chip) through `useWidgetChrome(...)` (`dashboard/chrome.ts`), compared field-wise so a 4 Hz value never re-renders the frame. `Tile` stays as an adapter over `PanelFrame` and must never be nested in a widget. Title precedence: document `title` › chrome `title` › `kind.titleFor` › (edit mode) the kind's label. Widgets without a title of their own (health strip, heading, spacer, link) have no title row in view mode; in edit mode their grip and menu float over the top-right corner (`.fb-tile-editing-float`) so the body keeps its height.
  - **Severity without moving the box.** Warn = `border-color` + 1px inset shadow (reads as 2px); alarm = border + `inset 0 0 0 1px bg-1, 0 0 0 2px alarm` (reads as double); stale = 1px dashed. The content box is identical for every level, so a warning tile's value sits exactly where its neighbours' do (§2 said "2px solid/double"; the width change misaligned rows — deviation recorded here).
  - **Spacing.** Every length in `.fb-tile*`, `dashboard.css` and the widget bodies is a `--fb-space-*` token; the grid gutter is `--fb-gap` (12 comfortable / 8 compact) in both the view grid (`gap`) and react-grid-layout (`margin`, read from the token in `Grid.tsx`). Head height `--fb-tile-head-h` (28 / 24 compact). Typography per §1.3: title 14/600 line-height 1.25, subtitle/caption 12, readout-m `clamp(22px, 11cqi, 40px)` weight 500, unit .5em on the baseline.
  - **Heights hold their content.** Body px at `h` rows with a title row = `36h − 66` (24px row + 12 gap − 2 border − 28 head − 24 padding). `.fb-tile-body` is `overflow: hidden`; nothing scrolls. Lists (events, program events) show `rowsThatFit(h)` rows; charts measure their host (`useChartHeight`, watching the legend too) instead of computing from `h`. Measured on `furnace.toml` at 1440 and 2341 (`$S/tools/measure-widgets.mjs`, `content-h.mjs`; body px available / content used):

    | kind | default | body px | content | minH (fits) | notes |
    |---|---|---|---|---|---|
    | readout | 6×5 | 114 | 114 | 4 (78: value + bar, sparkline dropped below h=5) | §3.1 said min 4×3; 42px cannot hold a 40px readout-m line |
    | gauge | 6×6 | 150 | drawing 124 + number | 4 | |
    | chart | 12×8 | 222 | plot 190 + 1-line legend | 5 | > 4 channels → full width in `generate.ts` (a 12-channel legend in 12 columns was 7 rows / 72px plot); ≤ 8 channels per chart |
    | health | 24×2 | 34 | 34 | 2 | equal columns split by hairlines; no conditions list (that is §3.8's widget) |
    | program | 8×6 | 150 | 28 status + 4 bar + 5 × 20 | 3 | |
    | events | 12×6 | 150 | 5 × 25 + 20 "all events" | 3 | |
    | recording | 6×3 | 42 | one 40px row | 3 | §3.10 min 4×2 raised to 4×3: two rows never fit 42px |
    | text | 6×4 | 78 | default note 64 | 2 | clips rather than scrolls |
    | heading / link | 24×1 / 4×1 | 22 (no head, no padding) | 22 | 1 | `body="none"` |
    | loop | 8×8 | 222 | 313 at 2341, 457 at 1440 | — | **still overflows** (controllers' `LoopPanel`: trends need to size to the remaining height, or drop when stacked) |
    | actuator | 6×5 | 114 | 104 | 3 | `▾ STATE` summary still rendered (§3.5 wants fields only) |
  - **Overlay.** `ChartOverlay` has a real "Close ✕" button (bg-2 on the bg-3 panel, `--fb-shadow-3`), takes focus on open, closes on Esc / Close / ⤡ / backdrop, and returns focus to the chart's expand button (captured by a capture-phase `focusin` before `MultiSeries` re-mounts its toolbar into the overlay).
  - Verified: `dash-e2e.mjs` ALL PASS; typecheck/build/test clean (453.4 kB gz); `#/dashboards` 0 console errors/warnings at 2341 dark, 1440 light, 400, edit mode, overlay; furnace: 0 of 14 non-loop widgets overflow; plant (101 widgets): only the 17 loops overflow.

- **16 Sep 2026 — the app on the device model (`temp-docs/DEVICE-MODEL-PLAN.md` §5; step 6, part 3).** Everything the app names is an **address** (`furnace.zone1`), a device name, or a controller name (the address of the signal it drives, `heaters.heater1`); `source`/`channel`/`measurand`, `reader`/`actuator` and `loop` are gone from `apps/dashboard`. Pages: `Sources` → **Inputs** (`#/inputs`: every publishing signal, grouped by device (`DeviceSignals`, namespaces as groups), by signal, or by unit — the toggle stays, stored as `flyball.inputs.view`; `#/inputs/<address>` is one signal: gauge/readout/trace for a publishing one, the write panel for a writable one, and the controllers that regulate or drive it); `Loops`+`Actuators` → **Controllers** (`Loops.tsx` deleted, `Controllers.tsx` is the page: one card per *writable* signal — the `ControllerPanel` faceplate when a controller drives it, else the `WritePanel` and an "Add controller" button; the stepper picks the target from `ControllerSchema.targets`, the source from `.sources` grouped by device, disabling what `driven`/`regulated` already spoke for); a **device page** `#/devices/<name>` (`DevicePanel`: state, conditions, run with Restart, the commands that are not simulation-only; `DeviceSignals`; the controllers on its signals) and `#/devices` (cards); `Actuators`/`Readers` pages gone. Router: `#/<page>[/<name>]`, no third segment (an address has no slashes); `hrefFor` handles `device | signal | controller | session | event`. Status bar reads `health.devices/controllers/waits/alarms` (the client-side alarm fallback is gone: `alarms` is required on the wire); the Overview's stat tiles are rig · recording · devices polling · controllers · conditions · waits · events · uptime, and it gained a Controllers section (a card per controller: source → target, mode, reading/target/demand).
  - **Dashboards** (§3, §5): documents are `schema_version: 2` — `readout`/`gauge` bind `address`, `chart` `addresses`, the `loop` widget (the wire keeps that kind name; its label is "Controller") binds `controller`, and the `actuator` widget is the **`device`** widget bound by `device` (state/conditions/run + chosen commands, `DevicePanel bare compact`); `readout.showSource` → `showDevice`; health tiles `readers/loops/signals` → `devices/controllers/waits`. `generate.ts` makes the same layout from `GET /api/devices` + `/api/controllers` (a device card only for devices with a writable signal; the simulation device is left out). The server migrates version-1 documents on read; the client's `SCHEMA_VERSION` is 2 and the presets `examples/simulated/dashboards/{overview,furnace}.json` are native v2 (the furnace preset's three actuator cards became one `heaters` device card). The server's `problems[]` on load/save now shows as a "N widgets need attention" chip in the page bar (tooltip lists them); the widgets still render their own "missing" state from the bindings. `Bindings` is `{devices, signals (publishing only), controllers, deviceLabel, signalLabel, signalAt}`; the traces/states contexts are gone — widgets read samples and write states from the store.
  - **Programs**: the builder's `command` step picks `device` (devices with commands) → `device_command` → that command's own `args` form, and the `set` step picks `device` (devices with a writable signal) → one number field per writable signal (label, unit, limits, `address [W]` as the hint), both from `GET /api/schema`; a new device clears the dependent fields and remounts the form (`onDeviceChange`). The `loop` field is titled "Controllers" and offers `GET /api/controllers` names. `ProgramCheck.warnings` (a step naming a controller, tuning or device the rig lacks) shows per step (amber border + alert), in the check chip (`N warnings`), and in the list's check column. Ported from branch `program-check-and-command-ui` (`actuator` → `device`).
  - **Simulation** page: `sim.device` is always on the wire; the application's own device is the `kind: "simulation"` entry of `GET /api/devices` and is rendered with `DevicePanel`; plant readings and stats are keyed by address; faults/disturbances come from every device's `simulation` commands.
  - `app.css`: `section + section` spacing is scoped to `main > section` — the library's panels are `<section>`s too, and the global rule staggered `DeviceSignals`' tiles.
  - Verified on `examples/simulated/furnace.yaml` (API 8143 / UI 5243): `npm run typecheck` 0 errors, `npm run build` 456.4 kB gz, `npm test` 36 passed, `dash-e2e.mjs` ALL PASS (its "Channel" pick is now "Signal"), every page (`#/`, `#/inputs`, `#/controllers`, `#/devices/furnace`, `#/programs`, `#/events`, `#/sessions`, `#/simulation`, `#/dashboards`, `#/graph`) `errors=0 warnings=0`.
  - Still open: the "By thing" picker (§4.3) would now be a device tree; the Controllers page's card shows no direct-demand box while a controller is attached (the rig refuses a manual demand in any mode — detach to drive by hand); `UnitCharts`' legend and subtitle name signals by address, not label.

- **17 Sep 2026 — every example rig through every page, then a fix cycle (commit `9d63b1a` and the cycle-2 follow-up).** Three read-only survey agents swept the 13 simulated/stress rigs plus the humidity sim at 1440 light, dark and 400 px (`scripts/ui-check/sweep.sh`, now including `#/rig`); the findings are in `/tmp/flyball-check/ISSUES*.md` and the fixes were made by four agents on disjoint files plus the orchestrator.
  - **Titles (§1, the labels rule).** `signalTitle(signal, devices)` / `signalTitleAt(signal, place)` in `@flyball/client`: a signal shown out of its tree is its own label when the driver gave one no other signal of the device shares, else its namespace then its name (`Dry line humidity`, never three `Humidity`). Used by the Graph picker and legend, the Chart/Readout/Gauge widgets, the dashboard `signalLabel`, Inputs, the Overview tiles, `UnitCharts` and the controller cards (which had double-qualified). The data side grew `tags` on `SignalOverride`/`NamespaceOverride` and `label`/`tags` on sim ports, so `examples/humidity` now carries `line: chamber|dry|wet` tags.
  - **Tag filter (Graph).** One chip row per tag axis across the rig's publishing signals (`tagAxes`, `hasTags`); an axis with nothing ticked does not filter; the search matches tag values too.
  - **Housekeeping.** `isHousekeeping`: a device's `conditions` output and everything under its `last` namespace. Hidden from tiles, pickers, generated dashboards, the device header's signal count and the seeding of latest values. `Chart` tells a signal that is absent from one that is present but not a number.
  - **Charts.** Live follows the right edge (`navigation.xRange` no longer fills from the left with an empty window ahead); tick labels never repeat (`tickDigits`, and `fixed` never prints `-0.0`) on `MultiSeries`, `TimeSeries` and the controller mini trends; a dimensionless axis is titled by its quantity (`Effort`); a filling chart observes its legend and gives back the rows it wraps to.
  - **Store.** `seed` also reads `/api/devices` once and takes each signal's `latest` (a mode set before the page opened showed `—`); history is asked only for signals a session declared (the daemon answered 409/404 per signal otherwise).
  - **Pages.** `#/inputs` renders the inputs grid (it was blank; the signal detail's breadcrumb led there). Controllers: faceplates first, then "Demands without a controller", the source named by title (`regulates Chamber humidity`), the per-demand button outlined. Programs: description on its own clamped line, notes as words (`from anneal.yaml`), the failed run's text once. Events reflow to stacked rows below 600 px; Simulation and Sessions tables carry a scroll shadow (`.fb-scroll-shadow-x`). Sessions trusts `/api/recording` for the open session and says when times are the rig's accelerated clock. The failed-program status chip is bounded (360 / 180 px) with an ellipsis; dot chips are icon-only below `sm`; `ChartControls` collapse into a popover below `sm` (the page bar at 400 px went from three rows to one). `WritePanel` seeds its entry once the readback is known rather than with 0. `LastSample` says nothing while streams connect.
  - **Entry.** `main.tsx` keeps one React root per container: Vite re-executing the entry on a hot update that reached it was the source of the intermittent `createRoot() … already passed` / `removeChild` page errors the sweeps hit while files were being edited.
  - Verified: humidity sim, all 11 pages × {1440, dark, 400}: 0 console errors/warnings/pageerrors; `npm test` 72; `tsc -b` clean; `dash-e2e.mjs` ALL PASS on furnace.
  - Still open: Programs table at 400 px (needs the Events treatment); `WritePanel`'s caption shows the device name, not its label; a signal named `set_voltage` gets a synthesised `Set set voltage`.
