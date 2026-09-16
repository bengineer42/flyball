import { memo, type ReactNode } from "react";
import { Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";

export const WINDOWS: Array<{ s: number; label: string }> = [
  { s: 60, label: "1 min" },
  { s: 300, label: "5 min" },
  { s: 900, label: "15 min" },
  { s: 3600, label: "1 h" },
];

/** One height and type size for every control on a chart toolbar, so they sit on one baseline. */
export const controlSx = { fontSize: "0.85rem", "& .MuiSelect-select": { py: 0.5 }, "& .MuiInputBase-input": { py: 0.5, fontSize: "0.85rem" } } as const;

/** A segmented control: 28px tall, so a row of them shares a baseline with a page heading. */
export const segmentSx = { "& .MuiToggleButton-root": { py: 0, px: 1, height: 28, textTransform: "none", fontSize: "0.8rem", lineHeight: 1, whiteSpace: "nowrap" } } as const;

/** A caption and the control it names, on one line. */
export function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75}>
      <Typography variant="caption" color="text.secondary" sx={{ lineHeight: 1, whiteSpace: "nowrap" }}>
        {label}
      </Typography>
      {children}
    </Stack>
  );
}

/**
 * How many seconds of history the charts show; they scroll once it is full.
 * Memoised: the pages that hold this re-render on every sample.
 */
export const WindowSelect = memo(function WindowSelect({ value, onChange }: { value: number; onChange(s: number): void }) {
  return (
    <Labelled label="window">
      <ToggleButtonGroup exclusive size="small" value={value} onChange={(_e, v: number | null) => v !== null && onChange(v)} aria-label="window" sx={segmentSx}>
        {WINDOWS.map((w) => (
          <ToggleButton key={w.s} value={w.s} aria-label={w.label}>
            {w.label.replace(" min", "m").replace(" h", "h")}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </Labelled>
  );
});
