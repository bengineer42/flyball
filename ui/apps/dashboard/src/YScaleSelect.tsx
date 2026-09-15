import { memo, useState } from "react";
import { FormControl, InputLabel, MenuItem, Select, Stack, TextField } from "@mui/material";
import type { YScale } from "@flyball/react";
import { WindowSelect } from "./WindowSelect.js";

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
  /** Shown after the min/max boxes as a hint, e.g. the first channel's unit. */
  unit?: string;
}

/**
 * How the charts' y axes are scaled: fit the data, the channel's declared
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
      label={key}
      value={custom[key]}
      onChange={(e) => apply({ ...custom, [key]: e.target.value })}
      inputProps={{ "aria-label": `y ${key}`, step: "any", style: { width: "5em" } }}
      sx={{ "& .MuiInputBase-input": { py: 0.5, fontSize: "0.85rem" } }}
      helperText={undefined}
    />
  );
  return (
    <Stack direction="row" spacing={0.75} alignItems="center">
      <FormControl size="small" sx={{ minWidth: 112 }}>
        <InputLabel id="yscale-select-label">y axis</InputLabel>
        <Select
          labelId="yscale-select-label"
          label="y axis"
          value={choice}
          onChange={(e) => choose(e.target.value as Choice)}
          sx={{ fontSize: "0.85rem", "& .MuiSelect-select": { py: 0.5 } }}
          inputProps={{ "aria-label": "y axis scale" }}
        >
          <MenuItem value="auto">Auto</MenuItem>
          <MenuItem value="range">Full range</MenuItem>
          <MenuItem value="custom">Custom…</MenuItem>
        </Select>
      </FormControl>
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
    <FormControl size="small" sx={{ minWidth: 110 }}>
      <InputLabel id="every-label">sample</InputLabel>
      <Select labelId="every-label" label="sample" value={value} onChange={(e) => onChange(Number(e.target.value))} inputProps={{ "data-testid": "every-select" }}>
        {EVERY.map((n) => (
          <MenuItem key={n} value={n}>
            {n === 1 ? "every point" : `every ${n}th`}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
});

/** The chart controls side by side; memoised for the same reason as `WindowSelect`. */
export const ChartControls = memo(function ChartControls({ windowS, onWindow, yScale, onYScale, every, onEvery, unit }: ChartSettings & { unit?: string }) {
  return (
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <YScaleSelect value={yScale} onChange={onYScale} unit={unit} />
      <EverySelect value={every} onChange={onEvery} />
      <WindowSelect value={windowS} onChange={onWindow} />
    </Stack>
  );
});
