/**
 * A non-numeric signal's value as a compact readout: bool on/off, str/enum a
 * chip, json a compact block -- never a gauge or a series (those are for
 * float/int only). Shared between Overview's tiles and the Readout widget,
 * which differ only in how they frame it (a `PanelFrame` of their own, or
 * chrome handed up through `useWidgetChrome`).
 */
import type { ReactNode } from "react";
import { Chip, Typography } from "@mui/material";
import { useFreshness, useLatestValue, type PanelSeverity } from "@flyball/react";
import { alarmLevel, type SignalOut } from "@flyball/client";

export interface ValueReadout {
  level: PanelSeverity;
  footer?: string;
  body: ReactNode;
}

/** Whether a signal's value belongs on a chart axis or a gauge dial; the rest are a chip or a block. */
export const isNumeric = (signal: Pick<SignalOut, "dtype">): boolean => signal.dtype === "float" || signal.dtype === "int";

/** `signal`'s live value, dtype-rendered, with the same staleness a numeric `Readout` would show. */
export function useValueReadout(signal: SignalOut | undefined): ValueReadout {
  const point = useLatestValue(signal?.address);
  const fresh = useFreshness(signal?.address);
  const level = signal ? alarmLevel(null, signal, fresh) : "ok";
  const ageS = fresh.lastSampleS != null && fresh.nowS != null ? Math.round(fresh.nowS - fresh.lastSampleS) : null;
  const footer = level === "stale" ? `last sample ${ageS} s ago` : undefined;
  const value = point?.value;
  const body =
    value === undefined || value === null ? (
      <span className="fb-muted">—</span>
    ) : signal?.dtype === "bool" ? (
      <Chip size="small" label={value ? "on" : "off"} color={value ? "success" : "default"} variant="outlined" />
    ) : signal?.dtype === "json" ? (
      <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.78em", lineHeight: 1.4, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {JSON.stringify(value)}
      </Typography>
    ) : (
      <Chip size="small" label={String(value)} variant="outlined" />
    );
  return { level, footer, body };
}
