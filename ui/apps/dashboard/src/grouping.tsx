import { memo } from "react";
import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import { segmentSx } from "./WindowSelect.js";

/** How the sources are laid out: a card per source, a flat grid of channels, or channels grouped by unit. */
export type Grouping = "source" | "channel" | "unit";

const KEY = "flyball.sources.view";

/** Remembered like the theme: one choice for the Overview and the Sources page. */
export const readGrouping = (): Grouping => {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "unit" || v === "channel" ? v : "source";
  } catch {
    return "source";
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
    <ToggleButtonGroup exclusive size="small" value={value} onChange={(_e, v: Grouping | null) => v && onChange(v)} aria-label="group sources" sx={segmentSx}>
      <ToggleButton value="source">by source</ToggleButton>
      <ToggleButton value="channel">by channel</ToggleButton>
      <ToggleButton value="unit">by unit</ToggleButton>
    </ToggleButtonGroup>
  );
});
