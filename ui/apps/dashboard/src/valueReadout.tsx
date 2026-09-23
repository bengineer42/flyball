/**
 * A non-numeric signal's value as a compact readout: bool on/off, str/enum a
 * chip, json a compact block -- never a gauge or a series (those are for
 * float/int only). Shared by the Readout widget,
 * which differ only in how they frame it (a `PanelFrame` of their own, or
 * chrome handed up through `useWidgetChrome`).
 */
import type { ReactNode } from "react";
import { Chip, Typography } from "@mui/material";
import { useFreshness, useLatestValue, type PanelSeverity } from "@flyball/react";
import { alarmLevel, humanise, type SignalOut } from "@flyball/client";

export interface ValueReadout {
  level: PanelSeverity;
  footer?: string;
  body: ReactNode;
}

/** Whether a signal's value belongs on a chart axis or a gauge dial; the rest are a chip or a block. */
export const isNumeric = (signal: Pick<SignalOut, "dtype">): boolean => signal.dtype === "float" || signal.dtype === "int";

/** A scalar inside a json value, as a person would write it: a type or a mode as a word, a number as itself. */
const scalar = (v: unknown): string => (typeof v === "string" ? humanise(v) : typeof v === "number" || typeof v === "boolean" ? String(v) : v === null ? "—" : JSON.stringify(v));

/**
 * A json value as a reading. A flat object -- a config such as a
 * blend flow `{flow: 1, on_overdrive: "clamp"}`, a command record -- is a
 * row per key (`Flow 1`, `On overdrive Clamp`), its `type` first as the
 * kind; anything deeper is shown as JSON, since that is what it is.
 */
export function JsonValue({ value }: { value: unknown }) {
  const flat = value !== null && typeof value === "object" && !Array.isArray(value) && Object.values(value).every((v) => v === null || typeof v !== "object");
  if (!flat)
    return (
      <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.78em", lineHeight: 1.4, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
        {JSON.stringify(value)}
      </Typography>
    );
  const entries = Object.entries(value as Record<string, unknown>);
  const type = entries.find(([k]) => k === "type");
  const rest = entries.filter(([k]) => k !== "type");
  return (
    <dl className="fb-json-rows">
      {type && (
        <div>
          <dt>kind</dt>
          <dd>{scalar(type[1])}</dd>
        </div>
      )}
      {rest.map(([k, v]) => (
        <div key={k}>
          <dt>{humanise(k)}</dt>
          <dd>{scalar(v)}</dd>
        </div>
      ))}
      {entries.length === 0 && <span className="fb-muted">empty</span>}
    </dl>
  );
}

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
      // The same chip a value takes, so a tile keeps its height with nothing to show (before the first sample, or at a paused moment with none).
      <Chip size="small" label="—" variant="outlined" className="fb-muted" />
    ) : signal?.dtype === "bool" ? (
      <Chip size="small" label={value ? "on" : "off"} color={value ? "success" : "default"} variant="outlined" />
    ) : signal?.dtype === "json" ? (
      <JsonValue value={value} />
    ) : (
      <Chip size="small" label={String(value)} variant="outlined" />
    );
  return { level, footer, body };
}
