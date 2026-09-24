import { useMemo, useState } from "react";
import { Box, Button, Chip, List, ListItemButton, ListItemText, Stack, TextField, Typography } from "@mui/material";
import TuneIcon from "@mui/icons-material/Tune";
import type { LawConfig, TuningChoice } from "@flyball/client";
import { ValueView } from "@flyball/react";

/** `kp 5 · ki 0.017 · tt 30`: a tuning's gains on one line, the type left out. */
export function gainsSummary(config: LawConfig): string {
  const parts = Object.entries(config)
    .filter(([k, v]) => k !== "type" && (typeof v === "number" || typeof v === "string" || typeof v === "boolean"))
    .map(([k, v]) => `${k} ${typeof v === "number" ? (Number.isInteger(v) ? v : Number(v.toPrecision(4))) : String(v)}`);
  return parts.length ? parts.join(" · ") : "no gains";
}

export interface TuningPickerProps {
  tunings: TuningChoice[];
  /** The chosen tuning's name; "" for none. */
  value: string;
  onChange(name: string): void;
  /** Offered on the chosen tuning: copy its config somewhere editable. */
  onConfigure?(config: LawConfig): void;
  /** Only tunings for these laws are shown at all (a loop that already has a law of one kind, say). */
  laws?: string[];
}

/**
 * Stored tunings as a list -- name, law type, gains -- filterable by law
 * (chips) and by text; the chosen one shows its config read-only, with a
 * button to carry that config into a form. Used wherever a tuning is picked
 * by name: the add-loop dialog's law step.
 */
export function TuningPicker({ tunings, value, onChange, onConfigure, laws }: TuningPickerProps) {
  const [law, setLaw] = useState<string | null>(null);
  const [text, setText] = useState("");
  const offered = useMemo(() => (laws ? tunings.filter((t) => laws.includes(t.law)) : tunings), [tunings, laws]);
  const tags = useMemo(() => [...new Set(offered.map((t) => t.law))].sort(), [offered]);
  const needle = text.trim().toLowerCase();
  const shown = offered.filter((t) => (law === null || t.law === law) && (!needle || `${t.name} ${gainsSummary(t.config)}`.toLowerCase().includes(needle)));
  const chosen = offered.find((t) => t.name === value) ?? null;

  return (
    <Stack spacing={1.5} data-testid="tuning-picker">
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
        <TextField
          size="small"
          placeholder="filter by name or gain"
          value={text}
          onChange={(e) => setText(e.target.value)}
          inputProps={{ "aria-label": "filter tunings" }}
          sx={{ minWidth: 180 }}
        />
        {tags.length > 1 && (
          <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap aria-label="filter by law">
            <Chip label="all" size="small" variant={law === null ? "filled" : "outlined"} onClick={() => setLaw(null)} />
            {tags.map((t) => (
              <Chip key={t} label={t} size="small" variant={law === t ? "filled" : "outlined"} color={law === t ? "primary" : "default"} onClick={() => setLaw(law === t ? null : t)} />
            ))}
          </Stack>
        )}
      </Stack>
      {shown.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          {offered.length === 0 ? "No stored tunings." : "No tuning matches."}
        </Typography>
      )}
      <List dense disablePadding sx={{ maxHeight: 220, overflowY: "auto", border: 1, borderColor: "divider", borderRadius: 1 }}>
        {shown.map((t) => (
          <ListItemButton key={t.name} selected={t.name === value} onClick={() => onChange(t.name)} data-tuning={t.name}>
            <ListItemText primary={t.name} secondary={gainsSummary(t.config)} primaryTypographyProps={{ fontWeight: t.name === value ? 600 : undefined }} />
            <Chip label={t.law} size="small" variant="outlined" sx={{ ml: 1.5 }} />
          </ListItemButton>
        ))}
      </List>
      {chosen && (
        <Box sx={{ px: 1.5, py: 0.75, bgcolor: "action.hover", borderRadius: 1 }} data-testid="tuning-detail">
          <Stack direction="row" alignItems="center" spacing={1.5} flexWrap="wrap" useFlexGap>
            <Typography variant="subtitle2">
              {chosen.name} <Chip label={chosen.law} size="small" variant="outlined" />
            </Typography>
            {onConfigure && (
              <Button size="small" startIcon={<TuneIcon />} onClick={() => onConfigure(chosen.config)} sx={{ ml: "auto !important" }} data-testid="configure-from-tuning">
                Configure from this
              </Button>
            )}
          </Stack>
          <ValueView value={Object.fromEntries(Object.entries(chosen.config).filter(([k]) => k !== "type"))} />
        </Box>
      )}
    </Stack>
  );
}
