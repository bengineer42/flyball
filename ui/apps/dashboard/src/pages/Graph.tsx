import { useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Checkbox,
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
  ToggleButton,
  ToggleButtonGroup,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import TuneIcon from "@mui/icons-material/Tune";
import CloseIcon from "@mui/icons-material/Close";
import { describeSignal, deviceOf, type DeviceOut, type SignalOut } from "@flyball/client";
import { MultiSeries, groupByUnit, useTraceRef, type MultiSeriesTrace } from "@flyball/react";
import { PageBar } from "../PageBar.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { StateBlock } from "../cards.js";
import { segmentSx } from "../WindowSelect.js";
import { publishingOf } from "./Inputs.js";
import { isNumeric } from "../valueReadout.js";

export interface GraphProps extends ChartSettings {
  devices: DeviceOut[];
}

type PickerGroup = "device" | "unit";

const GROUP_KEY = "flyball.graph.group";
const SELECTION_KEY = "flyball.graph.selection";
const HASH_PARAM = "ch";

const readPickerGroup = (): PickerGroup => {
  try {
    return window.localStorage.getItem(GROUP_KEY) === "unit" ? "unit" : "device";
  } catch {
    return "device";
  }
};
const writePickerGroup = (g: PickerGroup) => {
  try {
    window.localStorage.setItem(GROUP_KEY, g);
  } catch {
    /* not persisted */
  }
};

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
 * Selection lives in the URL hash (`?ch=a,b,c`, addresses, so a graph is
 * shareable) and in `localStorage` (so the last graph comes back on a plain
 * visit). Each signal keeps the colour slot it was first ticked into (spec
 * §1.2): hiding one never repaints the others, and re-ticking it returns
 * its own colour.
 */
export function Graph({ devices, ...charts }: GraphProps) {
  const { windowS, yScale, every } = charts;
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [pickerGroup, setPickerGroupState] = useState<PickerGroup>(readPickerGroup);
  const [search, setSearch] = useState("");

  // Every axis on this page is a `MultiSeries` line: a non-number never reaches it, so only numeric signals are offered.
  const publishing = useMemo(
    () =>
      publishingOf(devices)
        .map(({ device, signals }) => ({ device, signals: signals.filter(isNumeric) }))
        .filter((d) => d.signals.length > 0),
    [devices],
  );
  const byAddress = useMemo(() => new Map<string, SignalOut>(publishing.flatMap((d) => d.signals).map((s) => [s.address, s])), [publishing]);

  const [order, setOrder] = useState<string[]>(() => {
    const fromHash = readHashSelection();
    return (fromHash.length ? fromHash : readStoredSelection()).filter((k) => byAddress.has(k));
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
  const setPickerGroup = (g: PickerGroup) => {
    setPickerGroupState(g);
    writePickerGroup(g);
  };

  const deviceLabel = (name: string) => devices.find((d) => d.name === name)?.label ?? name;
  const needle = search.trim().toLowerCase();
  const matches = (s: SignalOut) => !needle || `${s.label} ${s.address} ${s.quantity} ${s.unit} ${deviceLabel(deviceOf(s.address))}`.toLowerCase().includes(needle);

  const branches =
    pickerGroup === "device"
      ? publishing.map(({ device, signals }) => ({ key: device.name, heading: device.label ?? device.name, signals: signals.filter(matches) }))
      : groupByUnit(publishing.flatMap((d) => d.signals).filter(matches)).map((g) => ({ key: g.unit, heading: g.unit, signals: g.signals }));
  const visibleBranches = branches.filter((b) => b.signals.length > 0);

  const selected = order.map((k) => byAddress.get(k)).filter((s): s is SignalOut => !!s);
  const live = useTraceRef(useMemo(() => selected.map((s) => s.address), [selected]));
  const primaryUnit = selected[0]?.unit;
  const series: MultiSeriesTrace[] = selected.map((s) => ({
    label: `${deviceLabel(deviceOf(s.address))}.${describeSignal(s)}`,
    unit: s.unit,
    key: s.address,
    color: seriesColorFor(slotFor(s.address)),
    precision: s.precision ?? undefined,
    hint: `${s.address} (${s.unit})`,
  }));

  const picker = (
    <Stack sx={{ height: "100%", minHeight: 0 }}>
      <Box sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1 }}>
        <TextField size="small" placeholder="search signals" value={search} onChange={(e) => setSearch(e.target.value)} inputProps={{ "aria-label": "search signals" }} />
        <ToggleButtonGroup exclusive size="small" value={pickerGroup} onChange={(_e, v: PickerGroup | null) => v && setPickerGroup(v)} aria-label="group signals" sx={segmentSx}>
          <ToggleButton value="device" sx={{ flex: 1 }}>
            by device
          </ToggleButton>
          <ToggleButton value="unit" sx={{ flex: 1 }}>
            by unit
          </ToggleButton>
        </ToggleButtonGroup>
      </Box>
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
                          {describeSignal(s)} <span className="fb-muted">{s.unit}</span>
                        </span>
                      }
                      secondary={pickerGroup === "unit" ? deviceLabel(deviceOf(s.address)) : undefined}
                    />
                  </ListItemButton>
                );
              })}
            </ul>
          </li>
        ))}
        {visibleBranches.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 1.5 }}>
            nothing matches “{search}”
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
        {selected.length === 0 ? "no signals selected" : `${selected.length} signal${selected.length === 1 ? "" : "s"} plotted`}
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
          {selected.length === 0 ? (
            <StateBlock state="empty" message="Tick a signal on the left to plot it." />
          ) : (
            <MultiSeries series={series} source={live} id="graph" unit={primaryUnit} title="graph" height="fill" windowS={windowS} yScale={yScale} every={every} />
          )}
        </div>
      </div>
    </>
  );
}
