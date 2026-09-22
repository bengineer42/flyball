import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import TuneIcon from "@mui/icons-material/Tune";
import CloseIcon from "@mui/icons-material/Close";
import { describeUnit, deviceOf, humanise, signalTitle, tagAxes, unitTitle, type ControllerOut, type DeviceOut, type SignalOut } from "@flyball/client";
import { MultiSeries, useControllers, useTraceRef, controllerSetpointKey, controllerNameFromSetpointKey, type MultiSeriesTrace } from "@flyball/react";
import { PageBar } from "../PageBar.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { StateBlock } from "../cards.js";
import { publishingOf } from "./Inputs.js";
import { isNumeric } from "../valueReadout.js";

export interface GraphProps extends ChartSettings {
  devices: DeviceOut[];
}

const SELECTION_KEY = "flyball.graph.selection";
const HASH_PARAM = "ch";


const readStoredSelection = (): string[] => {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(SELECTION_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === "string") : [];
  } catch {
    return [];
  }
};
const writeStoredSelection = (keys: string[]) => {
  try {
    window.localStorage.setItem(SELECTION_KEY, JSON.stringify(keys));
  } catch {
    /* not persisted */
  }
};

/** `#/graph?ch=a,b,c` → `["a", "b", "c"]` (signal addresses); empty when the hash carries none. */
const readHashSelection = (): string[] => {
  const query = window.location.hash.split("?")[1];
  if (!query) return [];
  const raw = new URLSearchParams(query).get(HASH_PARAM);
  return raw ? raw.split(",").filter(Boolean) : [];
};

/** Puts the selection in the URL hash without adding a history entry: a checkbox click must not spam Back. */
const writeHashSelection = (keys: string[]) => {
  const [path] = window.location.hash.split("?");
  const params = new URLSearchParams();
  if (keys.length) params.set(HASH_PARAM, keys.join(","));
  const query = params.toString();
  const next = `${path || "#/graph"}${query ? `?${query}` : ""}`;
  if (next !== window.location.hash) window.history.replaceState(null, "", next);
};

/** The series palette, read live so it follows the theme; a fallback for the first paint / no DOM. */
const FALLBACK_COLORS = ["#2a78d6", "#d95926", "#0f9a68", "#b87800", "#c9457a", "#008300", "#4a3aa7", "#e34948"];
function seriesColorFor(slot: number): string {
  const i = slot % FALLBACK_COLORS.length;
  if (typeof document === "undefined") return FALLBACK_COLORS[i]!;
  const value = getComputedStyle(document.documentElement).getPropertyValue(`--fb-series-${i + 1}`).trim();
  return value || FALLBACK_COLORS[i]!;
}

/**
 * Any readings plotted together: a searchable tree of every publishing
 * signal (grouped by device or by unit) on the left, ticked ones drawn on
 * one chart on the right with a y axis per distinct unit — spec §3.16's "no
 * dual axes" rule is deliberately overridden on this page, at the user's
 * request; `MultiSeries` itself caps it at four visible axes and folds the rest.
 *
 * A controller's setpoint can be ticked too, from its own "Setpoints" group
 * below the device branches (a controller is not a device, so the unit/tag
 * filters above don't apply to it, only the search box): it plots on the
 * axis of the signal it regulates (same unit, since a controller is named by
 * the signal it drives), dashed, labelled "‹signal title› (setpoint)" so it
 * reads apart from the measured line. Only the setpoint is offered, not the
 * controller's reading/demand/expected/correction -- this page plots
 * readings, not a controller-trace explorer (that is `ControllerPanel`'s
 * job, on the Controllers page). Keyed in the selection as
 * `controllerSetpointKey(name)`, distinct from any signal address (an
 * address never contains `:`).
 *
 * Selection lives in the URL hash (`?ch=a,b,c`, addresses or setpoint keys,
 * so a graph is shareable) and in `localStorage` (so the last graph comes
 * back on a plain visit). Each key keeps the colour slot it was first ticked
 * into (spec §1.2): hiding one never repaints the others, and re-ticking it
 * returns its own colour.
 */
export function Graph({ devices, ...charts }: GraphProps) {
  const { windowS, yScale } = charts;
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [search, setSearch] = useState("");
  // Filters: per axis (unit, device, and each tag axis such as line) the values ticked; an axis with none ticked does not filter.
  const [chosen, setChosen] = useState<Map<string, Set<string>>>(() => new Map());

  // Every axis on this page is a `MultiSeries` line: a non-number never reaches it, so only numeric signals are offered.
  const publishing = useMemo(
    () =>
      publishingOf(devices)
        .map(({ device, signals }) => ({ device, signals: signals.filter(isNumeric) }))
        .filter((d) => d.signals.length > 0),
    [devices],
  );
  const byAddress = useMemo(() => new Map<string, SignalOut>(publishing.flatMap((d) => d.signals).map((s) => [s.address, s])), [publishing]);

  // Every controller whose regulated signal (`source`) is a numeric publishing signal on this rig --
  // that is the signal `byAddress` already knows, so the setpoint's unit/title/device come from it.
  const { controllers: controllersLive } = useControllers();
  const controllerList = useMemo(() => Object.values(controllersLive).filter((c) => byAddress.has(c.source)), [controllersLive, byAddress]);
  const controllerByKey = useMemo(() => new Map(controllerList.map((c) => [controllerSetpointKey(c.name), c])), [controllerList]);
  // A controller-setpoint key is recognised by its shape alone (no need for `controllerList`, which
  // seeds asynchronously): dropping a not-yet-loaded one here would lose it from the hash/localStorage
  // before `useControllers` ever gets a chance to resolve it. `controllerByKey` below is what actually
  // decides whether it renders, once it can.
  const isPlottable = (k: string) => byAddress.has(k) || controllerNameFromSetpointKey(k) !== null;

  const [order, setOrder] = useState<string[]>(() => {
    const fromHash = readHashSelection();
    return (fromHash.length ? fromHash : readStoredSelection()).filter(isPlottable);
  });
  useEffect(() => {
    writeHashSelection(order);
    writeStoredSelection(order);
  }, [order]);

  // Fixed colour slots by first-selected order (DESIGN-SPEC §1.2): never reassigned while the page is open,
  // so deselecting one signal does not repaint the survivors, and reselecting it returns its own colour.
  const slots = useRef(new Map<string, number>());
  const nextSlot = useRef(0);
  const slotFor = (key: string): number => {
    let slot = slots.current.get(key);
    if (slot === undefined) {
      slot = nextSlot.current++;
      slots.current.set(key, slot);
    }
    return slot;
  };
  order.forEach(slotFor); // claim slots for whatever the hash/localStorage restored, in that order, before any click

  const toggle = (key: string) => setOrder((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  const deviceLabel = (name: string) => devices.find((d) => d.name === name)?.label ?? name;
  const title = (s: SignalOut) => signalTitle(s, devices);
  // The filter rows: unit and device first, then every tag axis the rig's signals carry (`line: dry | wet | chamber`).
  const all = useMemo(() => publishing.flatMap((d) => d.signals), [publishing]);
  const axes = useMemo(() => {
    const rows = new Map<string, Array<{ value: string; label: string }>>();
    rows.set("unit", [...new Set(all.map((s) => s.unit))].map((u) => ({ value: u, label: unitTitle(u, all.filter((s) => s.unit === u)) })));
    rows.set(
      "device",
      publishing.map(({ device }) => ({ value: device.name, label: device.label ?? device.name })),
    );
    for (const [axis, values] of tagAxes(all)) rows.set(axis, values.map((v) => ({ value: v, label: humanise(v) })));
    return rows;
  }, [all, publishing]);
  const valueOf = (s: SignalOut, axis: string): string | undefined => (axis === "unit" ? s.unit : axis === "device" ? deviceOf(s.address) : s.tags?.[axis]);
  const passes = (s: SignalOut) => [...chosen].every(([axis, values]) => values.size === 0 || (valueOf(s, axis) !== undefined && values.has(valueOf(s, axis)!)));
  const toggleFilter = (axis: string, value: string) =>
    setChosen((prev) => {
      const next = new Map(prev);
      const values = new Set(next.get(axis) ?? []);
      if (values.has(value)) values.delete(value);
      else values.add(value);
      next.set(axis, values);
      return next;
    });
  const anyFilter = [...chosen.values()].some((v) => v.size > 0);
  const needle = search.trim().toLowerCase();
  const matches = (s: SignalOut) =>
    passes(s) && (!needle || `${title(s)} ${s.address} ${s.quantity} ${s.unit} ${Object.values(s.tags ?? {}).join(" ")} ${deviceLabel(deviceOf(s.address))}`.toLowerCase().includes(needle));

  // The list stays in device order under device headings: the filters narrow it, they never regroup it.
  const branches = publishing.map(({ device, signals }) => ({ key: device.name, heading: device.label ?? device.name, signals: signals.filter(matches) }));
  const visibleBranches = branches.filter((b) => b.signals.length > 0);
  // Select all / none acts on what the filters and the search currently show, never on hidden signals.
  const shown = visibleBranches.flatMap((b) => b.signals.map((s) => s.address));
  const shownSelected = shown.filter((k) => order.includes(k)).length;
  const selectShown = () => setOrder((prev) => [...prev, ...shown.filter((k) => !prev.includes(k))]);
  const deselectShown = () => setOrder((prev) => prev.filter((k) => !shown.includes(k)));

  // Controllers are not devices, so the unit/device/tag filter chips above don't apply to them --
  // only the search box does. "select all"/"none" stays signal-only (above): a setpoint is a much
  // rarer thing to bulk-select than a device's own signals, so it isn't worth the extra affordance.
  const controllerMatches = (c: ControllerOut, source: SignalOut) =>
    !needle || `${title(source)} setpoint ${c.name} ${c.source} ${source.unit} ${deviceLabel(deviceOf(source.address))}`.toLowerCase().includes(needle);
  const visibleControllers = controllerList
    .map((c) => ({ c, source: byAddress.get(c.source)! }))
    .filter(({ c, source }) => controllerMatches(c, source));

  // Each ticked key is either a signal or a controller's setpoint (its regulated signal's own trace,
  // read from `byAddress`, gives the setpoint its unit/title/device -- the two plot identically once
  // resolved, only the label, dash and store key differ).
  type PlotItem = { key: string; signal: SignalOut; setpoint?: ControllerOut };
  const items: PlotItem[] = order.flatMap((k) => {
    const signal = byAddress.get(k);
    if (signal) return [{ key: k, signal }];
    const setpoint = controllerByKey.get(k);
    const source = setpoint ? byAddress.get(setpoint.source) : undefined;
    return source ? [{ key: k, signal: source, setpoint }] : [];
  });
  const live = useTraceRef(useMemo(() => items.map((i) => i.key), [items]));
  const primaryUnit = items[0]?.signal.unit;
  // A legend entry is the signal's title, "(setpoint)" appended for a controller's; the device joins it
  // only when two plotted signal traces (or two plotted setpoint traces) would otherwise read the same --
  // a signal and its own setpoint never collide, the suffix already tells them apart.
  const titleCount = new Map<string, number>();
  for (const it of items) {
    const k = `${it.setpoint ? "sp:" : "s:"}${title(it.signal)}`;
    titleCount.set(k, (titleCount.get(k) ?? 0) + 1);
  }
  const series: MultiSeriesTrace[] = items.map((it) => {
    const base = title(it.signal);
    const disambiguated = (titleCount.get(`${it.setpoint ? "sp:" : "s:"}${base}`) ?? 0) > 1 ? `${base} · ${deviceLabel(deviceOf(it.signal.address))}` : base;
    return {
      label: it.setpoint ? `${disambiguated} (setpoint)` : disambiguated,
      unit: it.signal.unit,
      quantity: it.signal.quantity,
      key: it.key,
      color: seriesColorFor(slotFor(it.key)),
      precision: it.signal.precision ?? undefined,
      dash: it.setpoint ? true : undefined,
      hint: it.setpoint
        ? `setpoint of ${deviceLabel(deviceOf(it.signal.address))} · ${it.signal.address} (controller ${it.setpoint.name})`
        : `${deviceLabel(deviceOf(it.signal.address))} · ${it.signal.address}${describeUnit(it.signal.unit) ? ` (${describeUnit(it.signal.unit)})` : ""}`,
    };
  });

  const picker = (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Box sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1 }}>
        <TextField size="small" placeholder="search signals" value={search} onChange={(e) => setSearch(e.target.value)} inputProps={{ "aria-label": "search signals" }} />
        {[...axes].map(([axis, values]) =>
          values.length < 2 && axis !== "unit" ? null : (
            <Stack key={axis} direction="row" flexWrap="wrap" useFlexGap spacing={0.5} alignItems="center" aria-label={`filter by ${axis}`}>
              <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5, minWidth: "3.2em" }} title={`tick one or more to show only those signals`}>
                {humanise(axis)}
              </Typography>
              {values.map(({ value, label }) => {
                const on = chosen.get(axis)?.has(value) ?? false;
                return <Chip key={value} label={label} size="small" variant={on ? "filled" : "outlined"} color={on ? "primary" : "default"} onClick={() => toggleFilter(axis, value)} aria-pressed={on} />;
              })}
            </Stack>
          ),
        )}
        {anyFilter && (
          <Typography variant="caption" sx={{ alignSelf: "flex-start", cursor: "pointer", color: "primary.main" }} onClick={() => setChosen(new Map())} role="button">
            clear filters
          </Typography>
        )}
      </Box>
      <Divider />
      {shown.length > 0 && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 0.5 }}>
          <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>
            {shownSelected} of {shown.length} shown plotted
          </Typography>
          <Button size="small" variant="text" disabled={shownSelected === shown.length} onClick={selectShown} aria-label="select all shown signals">
            select all
          </Button>
          <Button size="small" variant="text" disabled={shownSelected === 0} onClick={deselectShown} aria-label="deselect all shown signals">
            none
          </Button>
        </Stack>
      )}
      <Divider />
      <List dense disablePadding sx={{ overflow: "auto", flex: "1 1 auto", minHeight: 0 }}>
        {visibleBranches.map((b) => (
          <li key={b.key}>
            <ul style={{ padding: 0 }}>
              <ListSubheader disableSticky sx={{ lineHeight: "28px" }}>
                {b.heading}
              </ListSubheader>
              {b.signals.map((s) => {
                const key = s.address;
                const checked = order.includes(key);
                return (
                  <ListItemButton key={key} dense onClick={() => toggle(key)} sx={{ py: 0.25 }}>
                    <ListItemIcon sx={{ minWidth: 32 }}>
                      <Checkbox edge="start" size="small" checked={checked} tabIndex={-1} disableRipple />
                    </ListItemIcon>
                    <ListItemText
                      primary={
                        <span title={s.address}>
                          {title(s)} <span className="fb-muted">{describeUnit(s.unit)}</span>
                        </span>
                      }
                    />
                  </ListItemButton>
                );
              })}
            </ul>
          </li>
        ))}
        {visibleControllers.length > 0 && (
          <li key="__setpoints">
            <ul style={{ padding: 0 }}>
              <ListSubheader disableSticky sx={{ lineHeight: "28px" }}>
                Setpoints
              </ListSubheader>
              {visibleControllers.map(({ c, source }) => {
                const key = controllerSetpointKey(c.name);
                const checked = order.includes(key);
                return (
                  <ListItemButton key={key} dense onClick={() => toggle(key)} sx={{ py: 0.25 }}>
                    <ListItemIcon sx={{ minWidth: 32 }}>
                      <Checkbox edge="start" size="small" checked={checked} tabIndex={-1} disableRipple />
                    </ListItemIcon>
                    <ListItemText
                      primary={
                        <span title={`setpoint of ${c.name}`}>
                          {title(source)} <span className="fb-muted">setpoint · {describeUnit(source.unit)}</span>
                        </span>
                      }
                    />
                  </ListItemButton>
                );
              })}
            </ul>
          </li>
        )}
        {visibleBranches.length === 0 && visibleControllers.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 1.5 }}>
            {needle ? `nothing matches “${search}”` : "no signal passes the ticked filters"}
          </Typography>
        )}
      </List>
    </Stack>
  );

  const bar = (
    <PageBar end={<ChartControls {...charts} unit={primaryUnit} />}>
      {narrow && (
        <IconButton size="small" aria-label="choose signals" onClick={() => setDrawerOpen(true)}>
          <TuneIcon fontSize="small" />
        </IconButton>
      )}
      <Typography variant="body2" color="text.secondary">
        {items.length === 0 ? "no series selected" : `${items.length} series${items.length === 1 ? "" : "s"} plotted`}
      </Typography>
    </PageBar>
  );

  if (publishing.length === 0)
    return (
      <>
        {bar}
        <StateBlock state="empty" message="No signal publishes. Add a device with a publishing signal to the rig file to plot it here." />
      </>
    );

  return (
    <>
      {bar}
      <div className="fb-graph-body">
        {!narrow && <div className="fb-graph-picker">{picker}</div>}
        {narrow && (
          <Drawer anchor="left" open={drawerOpen} onClose={() => setDrawerOpen(false)} PaperProps={{ sx: { width: 280, maxWidth: "85vw", display: "flex", flexDirection: "column" } }}>
            <Stack direction="row" alignItems="center" sx={{ px: 2, py: 1, borderBottom: 1, borderColor: "divider" }}>
              <Typography variant="h1" component="h2" sx={{ flexGrow: 1 }}>
                Signals
              </Typography>
              <IconButton aria-label="close" onClick={() => setDrawerOpen(false)}>
                <CloseIcon fontSize="small" />
              </IconButton>
            </Stack>
            <Box sx={{ flex: "1 1 auto", minHeight: 0, display: "flex" }}>{picker}</Box>
          </Drawer>
        )}
        <div className="fb-fill fb-chart-host fb-graph-chart">
          {items.length === 0 ? (
            <StateBlock state="empty" message="Tick a signal on the left to plot it." />
          ) : (
            <MultiSeries series={series} source={live} id="graph" unit={primaryUnit} title="graph" height="fill" windowS={windowS} yScale={yScale} />
          )}
        </div>
      </div>
    </>
  );
}
