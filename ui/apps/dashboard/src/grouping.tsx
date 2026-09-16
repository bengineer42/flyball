import { memo } from "react";
import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import { segmentSx } from "./WindowSelect.js";

/** How the publishing signals are laid out: a card per device, a flat grid of signals, or signals grouped by unit. */
export type Grouping = "device" | "signal" | "unit";

const KEY = "flyball.inputs.view";

/** Remembered like the theme: one choice for the Overview and the Inputs page. */
export const readGrouping = (): Grouping => {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "unit" || v === "signal" ? v : "device";
  } catch {
    return "device";
  }
};

export const writeGrouping = (g: Grouping) => {
  try {
    window.localStorage.setItem(KEY, g);
  } catch {
    /* not persisted */
  }
};

export const GroupingSelect = memo(function GroupingSelect({ value, onChange }: { value: Grouping; onChange(g: Grouping): void }) {
  return (
    <ToggleButtonGroup exclusive size="small" value={value} onChange={(_e, v: Grouping | null) => v && onChange(v)} aria-label="group signals" sx={segmentSx}>
      <ToggleButton value="device">by device</ToggleButton>
      <ToggleButton value="signal">by signal</ToggleButton>
      <ToggleButton value="unit">by unit</ToggleButton>
    </ToggleButtonGroup>
  );
});
