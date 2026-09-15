import { memo } from "react";
import { FormControl, InputLabel, MenuItem, Select } from "@mui/material";

export const WINDOWS: Array<{ s: number; label: string }> = [
  { s: 60, label: "1 min" },
  { s: 300, label: "5 min" },
  { s: 900, label: "15 min" },
  { s: 3600, label: "1 h" },
];

/**
 * How many seconds of history the charts show; they scroll once it is full.
 * Memoised: MUI's FormControl sets state in an effect on every render in
 * development, and the pages that hold this re-render on every sample.
 */
export const WindowSelect = memo(function WindowSelect({ value, onChange }: { value: number; onChange(s: number): void }) {
  return (
    <FormControl size="small" sx={{ minWidth: 104 }}>
      <InputLabel id="window-select-label">window</InputLabel>
      <Select
        labelId="window-select-label"
        label="window"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        sx={{ fontSize: "0.85rem", "& .MuiSelect-select": { py: 0.5 } }}
      >
        {WINDOWS.map((w) => (
          <MenuItem key={w.s} value={w.s}>
            {w.label}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
});
