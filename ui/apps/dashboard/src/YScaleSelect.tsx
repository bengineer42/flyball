import { memo, useState } from "react";
import { IconButton, Popover, Stack, TextField, ToggleButton, ToggleButtonGroup, useMediaQuery, useTheme } from "@mui/material";
import TuneIcon from "@mui/icons-material/Tune";
import type { YScale } from "@flyball/react";
import { Labelled, WindowSelect, controlSx, segmentSx } from "./WindowSelect.js";

const STORAGE_KEY = "flyball.charts.yscale";

export const readYScale = (): YScale => {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "range") return "range";
    if (raw && raw.startsWith("{")) {
      const v = JSON.parse(raw) as { min?: unknown; max?: unknown };
      if (typeof v.min === "number" && typeof v.max === "number") return { min: v.min, max: v.max };
    }
  } catch {
    /* no storage */
  }
  return "auto";
};

export const writeYScale = (s: YScale) => {
  try {
    window.localStorage.setItem(STORAGE_KEY, typeof s === "string" ? s : JSON.stringify(s));
  } catch {
    /* not persisted */
  }
};

type Choice = "auto" | "range" | "custom";

export interface YScaleSelectProps {
  value: YScale;
  onChange(s: YScale): void;
  /** Shown after the min/max boxes as a hint, e.g. the first signal's unit. */
  unit?: string;
}

/**
 * How the charts' y axes are scaled: fit the data, the signal's declared
 * range, or bounds typed here. Custom bounds apply once both are numbers
 * and min < max; until then the previous scale stays.
 */
export const YScaleSelect = memo(function YScaleSelect({ value, onChange, unit }: YScaleSelectProps) {
  const choice: Choice = typeof value === "object" ? "custom" : value;
  const [custom, setCustom] = useState<{ min: string; max: string }>(() => (typeof value === "object" ? { min: String(value.min), max: String(value.max) } : { min: "", max: "" }));
  const [editing, setEditing] = useState(choice === "custom");
  const apply = (next: { min: string; max: string }) => {
    setCustom(next);
    const min = Number(next.min), max = Number(next.max);
    if (next.min !== "" && next.max !== "" && Number.isFinite(min) && Number.isFinite(max) && min < max) onChange({ min, max });
  };
  const choose = (c: Choice) => {
    setEditing(c === "custom");
    if (c === "custom") apply(custom);
    else onChange(c);
  };
  const box = (key: "min" | "max") => (
    <TextField
      type="number"
      placeholder={key}
      value={custom[key]}
      onChange={(e) => apply({ ...custom, [key]: e.target.value })}
      inputProps={{ "aria-label": `y ${key}`, step: "any", style: { width: "5em" } }}
      sx={controlSx}
      helperText={undefined}
    />
  );
  return (
    <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
      <Labelled label="y">
        <ToggleButtonGroup exclusive size="small" value={choice} onChange={(_e, v: Choice | null) => v && choose(v)} aria-label="y axis scale" sx={segmentSx}>
          <ToggleButton value="auto" title="Fit the y axis to the data">auto</ToggleButton>
          <ToggleButton value="range" title="The signal's declared range">range</ToggleButton>
          <ToggleButton value="custom" title="Bounds typed here">custom</ToggleButton>
        </ToggleButtonGroup>
      </Labelled>
      {editing && (
        <>
          {box("min")}
          {box("max")}
          {unit && <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>{unit}</span>}
        </>
      )}
    </Stack>
  );
});

/** What every chart page takes: the window and the y scale, and how to change them. */
export interface ChartSettings {
  windowS: number;
  onWindow(s: number): void;
  yScale: YScale;
  onYScale(s: YScale): void;
}

/** The chart controls side by side, on one baseline; memoised for the same reason as `WindowSelect`. Lives once per page, in the page bar. */
export const ChartControls = memo(function ChartControls({ windowS, onWindow, yScale, onYScale, unit }: ChartSettings & { unit?: string }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const controls = (
    <>
      <WindowSelect value={windowS} onChange={onWindow} />
      <YScaleSelect value={yScale} onChange={onYScale} unit={unit} />
    </>
  );
  // At phone width three segmented rows would take the page bar's whole height: one button, the rows in a popover.
  if (narrow)
    return (
      <>
        <IconButton size="small" aria-label="chart settings" title="window, sampling and y axis" onClick={(e) => setAnchor(e.currentTarget)}>
          <TuneIcon fontSize="small" />
        </IconButton>
        <Popover open={anchor !== null} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
          <Stack spacing={1.5} sx={{ p: 1.5 }} alignItems="flex-start">
            {controls}
          </Stack>
        </Popover>
      </>
    );
  return (
    <Stack direction="row" spacing={3} alignItems="center" flexWrap="wrap" useFlexGap justifyContent="flex-end">
      {controls}
    </Stack>
  );
});
