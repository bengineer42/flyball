import { describeCaveats, describeQuality, type Caveats, type Quality, type Value } from "@flyball/client";

/** What a reading needs to say it has no value, and what it had last: a `SignalReading`, a `LatestOut` with its `last_usable`. */
export interface ReadingLike {
  value: Value;
  quality?: Quality;
  reason?: string;
  caveats?: Caveats;
  lastUsable?: { t: number; value: Value } | null;
}

/** A reading with no value as a badge reads it: the glyph in the number's place, the words, the hover. */
export interface NoValue {
  /** In the number's place: `…` while pending, `—` otherwise. */
  glyph: "—" | "…";
  quality: Quality;
  /** `stale: device silent`, `invalid: sensor open`, `n/a`, `pending`. */
  label: string;
  /** The last usable value and when, for the hover: `last usable 21.30 °C at 12:03:04`. */
  hint: string;
}

/** A rig timestamp (seconds) as a time of day. */
const clock = (s: number) => new Date(s * 1000).toLocaleTimeString();

/**
 * Why `reading` shows no number, or null when it has one (or there is no reading yet, which
 * shows as an empty tile). `format` writes the last usable value the way the tile writes its own.
 */
export function noValue(reading: ReadingLike | null | undefined, format: (value: Value) => string): NoValue | null {
  if (!reading) return null;
  const quality: Quality = reading.quality ?? (reading.value === null ? "invalid" : "ok");
  if (quality === "ok" && reading.value !== null) return null;
  const last = reading.lastUsable;
  const hint = last && last.value !== null ? `last usable ${format(last.value)} at ${clock(last.t)}` : "no usable reading yet";
  if (quality === "pending") return { glyph: "…", quality, label: "pending", hint };
  return { glyph: "—", quality, label: describeQuality(quality, reading.reason) || "no value", hint };
}

/** The words of `noValue`, as a small badge beside the `—`; faults (`invalid`, `stale`) read stronger than `n/a`/`pending`. */
export function QualityBadge({ state }: { state: NoValue }) {
  return (
    <span className={`fb-quality fb-quality-${state.quality}`} title={state.hint} data-testid="quality-badge">
      {state.label}
    </span>
  );
}

/**
 * A usable value's caveats, marked quietly beside it: `≥`/`≤` at a limit (the true value may lie
 * beyond it), `*` out of range; the words on hover. Nothing for none. Never an alarm colour: a
 * caveat gates nothing.
 */
export function CaveatMark({ caveats }: { caveats: Caveats | null | undefined }) {
  const text = describeCaveats(caveats);
  if (!text || !caveats) return null;
  const glyph = caveats.at_limit === "high" ? "≥" : caveats.at_limit === "low" ? "≤" : "*";
  return (
    <span className="fb-caveat" title={text} aria-label={text} data-testid="caveat-mark">
      {glyph}
    </span>
  );
}
