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

/** What every chart page takes: the window, the y scale and the sampling, and how to change them. */
export interface ChartSettings {
  windowS: number;
  onWindow(s: number): void;
  yScale: YScale;
  onYScale(s: YScale): void;
  /** Draw one point in `every`; 1 draws them all. */
  every: number;
  onEvery(n: number): void;
}

const EVERY = [1, 2, 5, 10, 20, 50];
const EVERY_KEY = "flyball.every";
export const readEvery = (): number => {
  try {
    const n = Number(localStorage.getItem(EVERY_KEY));
    return EVERY.includes(n) ? n : 1;
  } catch {
    return 1;
  }
};
export const writeEvery = (n: number) => {
  try {
    localStorage.setItem(EVERY_KEY, String(n));
  } catch {
    /* not persisted */
  }
};

/** Sample every nth point: lighter charts for a long window or a dense series. */
export const EverySelect = memo(function EverySelect({ value, onChange }: { value: number; onChange(n: number): void }) {
  return (
    <Labelled label="sample">
      <ToggleButtonGroup exclusive size="small" value={value} onChange={(_e, v: number | null) => v !== null && onChange(v)} aria-label="sample every nth point" data-testid="every-select" sx={segmentSx}>
        {EVERY.map((n) => (
          <ToggleButton key={n} value={n} title={n === 1 ? "Every point" : `Every ${n}th point`}>
            {n === 1 ? "all" : `1/${n}`}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </Labelled>
  );
});

/** The chart controls side by side, on one baseline; memoised for the same reason as `WindowSelect`. Lives once per page, in the page bar. */
export const ChartControls = memo(function ChartControls({ windowS, onWindow, yScale, onYScale, every, onEvery, unit }: ChartSettings & { unit?: string }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const controls = (
    <>
      <WindowSelect value={windowS} onChange={onWindow} />
      <EverySelect value={every} onChange={onEvery} />
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
