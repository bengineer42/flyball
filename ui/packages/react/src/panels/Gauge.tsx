import { alarmLevel, describeUnit, withUnit, type AlarmLevel, type SignalOut, fixed } from "@flyball/client";
import { CaveatMark, QualityBadge, noValue, type ReadingLike } from "./quality.js";

export type GaugeKind = "thermometer" | "tank" | "dial" | "bar";

export interface GaugeProps {
  signal: Bands & Pick<SignalOut, "unit" | "precision">;
  /** The current value; undefined or null draws an empty gauge. */
  value: number | null | undefined;
  /**
   * The newest reading with its quality (`useReading`): with no value the number reads "—" (or
   * "…" pending) and why, the last usable value on hover; a `stale` one makes the gauge stale.
   * Omitted, `value` alone is shown.
   */
  reading?: ReadingLike;
  /** Default by unit: temperatures a thermometer, percentages a tank, else a dial. */
  kind?: GaugeKind;
  /** Pixel height of the drawing (bars: of the strip). */
  height?: number;
  /** The rig's band condition on this signal (`useBandLevel`); without it the value is checked against the bands here. */
  band?: "ok" | "warn" | "alarm" | "unknown";
}

/** Zone and fill colours; an embedding page sets the variables. */
const COLOUR: Record<AlarmLevel, string> = {
  ok: "var(--fb-ok, #2e8b57)",
  warn: "var(--fb-warn, #e0a100)",
  alarm: "var(--fb-alarm, #b3261e)",
  unknown: "var(--fb-unknown, #6d5bb3)",
  stale: "var(--fb-stale, #8a93a2)",
};
const NEUTRAL = "var(--fb-border, #d8d8d8)";
const ACCENT = "var(--fb-accent, #2557a7)";

/** The gauge a unit suggests: temperatures a thermometer, percentages a tank, else a dial. */
export function gaugeKindFor(unit: string): GaugeKind {
  if (unit === "°C" || unit === "K" || unit === "°F") return "thermometer";
  if (unit === "%RH" || unit === "%") return "tank";
  return "dial";
}

/** What a gauge needs of a signal: its plausible range, its bands, and what a demand is clamped to. */
export type Bands = Pick<SignalOut, "range" | "warning" | "alarm" | "limits">;

/**
 * The signal's own range, else the widest band padded a tenth, else what a
 * demand is clamped to (`limits` -- a blend's 0–2 L/min flow has no `range`
 * or bands of its own, but does have limits, and those beat a meaningless
 * 0–100 default), else 0–100 for a signal with none of the three.
 */
export function gaugeRange(signal: Bands): [number, number] {
  if (signal.range) return signal.range;
  const band = signal.alarm ?? signal.warning;
  if (band) {
    const pad = (band[1] - band[0]) / 10 || 1;
    return [band[0] - pad, band[1] + pad];
  }
  if (signal.limits) return signal.limits;
  return [0, 100];
}

export interface GaugeZone {
  from: number;
  to: number;
  /** null when the signal has no bands: one neutral zone. */
  level: AlarmLevel | null;
}

/** The range cut at every band edge, each piece labelled with the level a value inside it has. */
export function gaugeZones(signal: Bands): GaugeZone[] {
  const [lo, hi] = gaugeRange(signal);
  if (!signal.warning && !signal.alarm) return [{ from: lo, to: hi, level: null }];
  const edges = new Set([lo, hi]);
  for (const band of [signal.warning, signal.alarm]) {
    if (band) for (const edge of band) if (edge > lo && edge < hi) edges.add(edge);
  }
  const sorted = [...edges].sort((a, b) => a - b);
  return sorted.slice(1).map((to, i) => {
    const from = sorted[i]!;
    return { from, to, level: alarmLevel((from + to) / 2, signal) };
  });
}

/** Characters the widest value in `range` needs at `precision` decimals, so a number holds its width. */
export function numberWidth(range: [number, number] | null, precision: number): number {
  const widest = range ? Math.max(Math.abs(range[0]), Math.abs(range[1])) : 9999;
  return String(Math.floor(widest)).length + (range && range[0] < 0 ? 1 : 0) + (precision ? precision + 1 : 0);
}

const zoneColour = (zone: GaugeZone) => (zone.level === null ? NEUTRAL : COLOUR[zone.level]);

/** One signal as a picture: its range with the warning/alarm zones, the value as a fill or needle, the number under it. */
export function Gauge({ signal, value: given, kind = gaugeKindFor(signal.unit), height, reading, band }: GaugeProps) {
  const range = gaugeRange(signal);
  const zones = gaugeZones(signal);
  const precision = signal.precision ?? 2;
  const none = noValue(reading, (v) => withUnit(fixed(v, precision), signal.unit));
  // Never the value before a reading with none.
  const value = none ? null : given;
  const level = alarmLevel(value, signal, band, reading?.quality);
  const hasBands = !!(signal.warning || signal.alarm);
  const fill = hasBands ? COLOUR[level] : ACCENT;
  const fraction = value === undefined || value === null ? null : Math.min(1, Math.max(0, (value - range[0]) / (range[1] - range[0])));
  const drawing = { range, zones, fraction, fill, height };
  return (
    <div
      className={`fb-gauge fb-gauge-${kind} fb-alarm-${level}`}
      title={none ? `${none.label} · ${none.hint}` : withUnit(`${range[0]} – ${range[1]}`, signal.unit)}
    >
      {kind === "thermometer" && <Thermometer {...drawing} />}
      {kind === "tank" && <Tank {...drawing} />}
      {kind === "dial" && <Dial {...drawing} />}
      {kind === "bar" && <Bar {...drawing} />}
      <div className="fb-gauge-value" style={{ color: (hasBands && level !== "ok" && level !== "stale") || level === "unknown" ? COLOUR[level] : undefined }}>
        {!none && <CaveatMark caveats={reading?.caveats} />}
        <span className="fb-gauge-number" style={{ minWidth: `${numberWidth(range, precision)}ch` }} title={none?.hint}>
          {none ? none.glyph : fixed(value, precision)}
        </span>
        <span className="fb-gauge-unit">{describeUnit(signal.unit)}</span>
      </div>
      {/* Always rendered, even blank: an appearing/disappearing footer would resize the tile every time the quality flips. */}
      <div className="fb-gauge-footer">{none ? <QualityBadge state={none} /> : " "}</div>
    </div>
  );
}

interface Drawing {
  range: [number, number];
  zones: GaugeZone[];
  fraction: number | null;
  fill: string;
  height?: number;
}

const tick = (n: number) => (Number.isInteger(n) ? String(n) : n.toPrecision(3));

/** A vertical strip of zones from `top` to `bottom` at `x`; the pieces of every vertical gauge. */
function ZoneStrip({ zones, range, x, width, top, bottom }: Drawing & { x: number; width: number; top: number; bottom: number }) {
  const y = (v: number) => bottom - ((v - range[0]) / (range[1] - range[0])) * (bottom - top);
  return (
    <g className="fb-gauge-zones">
      {zones.map((z) => (
        <rect key={z.from} x={x} width={width} y={y(z.to)} height={Math.max(0, y(z.from) - y(z.to))} fill={zoneColour(z)} />
      ))}
    </g>
  );
}

function Thermometer(d: Drawing) {
  const h = d.height ?? 160;
  const W = 72;
  const top = 10;
  const bottom = h - 30;
  const cx = 32;
  const bulbY = h - 16;
  const level = d.fraction === null ? bottom : bottom - d.fraction * (bottom - top);
  return (
    <svg className="fb-gauge-svg" width={W} height={h} viewBox={`0 0 ${W} ${h}`} role="img">
      <ZoneStrip {...d} x={44} width={5} top={top} bottom={bottom} />
      <rect x={cx - 7} y={top - 7} width={14} height={bottom - top + 12} rx={7} fill="var(--fb-panel, #f6f6f6)" stroke={NEUTRAL} />
      <circle cx={cx} cy={bulbY} r={12} fill="var(--fb-panel, #f6f6f6)" stroke={NEUTRAL} />
      {d.fraction !== null && (
        <>
          <rect x={cx - 4} y={level} width={8} height={bulbY - level} fill={d.fill} />
          <circle cx={cx} cy={bulbY} r={9} fill={d.fill} />
        </>
      )}
      <line x1={cx + 7} x2={cx + 10} y1={top} y2={top} stroke={NEUTRAL} />
      <line x1={cx + 7} x2={cx + 10} y1={bottom} y2={bottom} stroke={NEUTRAL} />
      <text className="fb-gauge-tick" x={cx - 11} y={top + 3} textAnchor="end">{tick(d.range[1])}</text>
      <text className="fb-gauge-tick" x={cx - 11} y={bottom + 3} textAnchor="end">{tick(d.range[0])}</text>
    </svg>
  );
}

function Tank(d: Drawing) {
  const h = d.height ?? 120;
  const W = 92;
  const top = 8;
  const bottom = h - 8;
  const level = d.fraction === null ? bottom : bottom - d.fraction * (bottom - top);
  return (
    <svg className="fb-gauge-svg" width={W} height={h} viewBox={`0 0 ${W} ${h}`} role="img">
      <rect x={8} y={top} width={48} height={bottom - top} rx={3} fill="var(--fb-panel, #f6f6f6)" stroke={NEUTRAL} />
      {d.fraction !== null && <rect x={8.5} y={level} width={47} height={bottom - level} fill={d.fill} />}
      <rect x={8} y={top} width={48} height={bottom - top} rx={3} fill="none" stroke={NEUTRAL} />
      <ZoneStrip {...d} x={60} width={5} top={top} bottom={bottom} />
      <text className="fb-gauge-tick" x={68} y={top + 3}>{tick(d.range[1])}</text>
      <text className="fb-gauge-tick" x={68} y={bottom + 3}>{tick(d.range[0])}</text>
    </svg>
  );
}

/** 240° of arc, from bottom-left over the top to bottom-right. */
function Dial(d: Drawing) {
  const h = d.height ?? 100;
  const W = 120;
  const cx = 60;
  const cy = 60;
  const r = 46;
  const point = (f: number, radius = r) => {
    const angle = ((210 - 240 * f) * Math.PI) / 180;
    return [cx + radius * Math.cos(angle), cy - radius * Math.sin(angle)] as const;
  };
  const arc = (f0: number, f1: number) => {
    const [x0, y0] = point(f0);
    const [x1, y1] = point(f1);
    return `M ${x0} ${y0} A ${r} ${r} 0 ${f1 - f0 > 0.75 ? 1 : 0} 1 ${x1} ${y1}`;
  };
  const frac = (v: number) => (v - d.range[0]) / (d.range[1] - d.range[0]);
  const needle = d.fraction === null ? null : point(d.fraction, r - 8);
  return (
    <svg className="fb-gauge-svg" width={(W * h) / 100} height={h} viewBox={`0 0 ${W} 100`} role="img">
      <g className="fb-gauge-zones" fill="none" strokeWidth={10}>
        {d.zones.map((z) => (
          <path key={z.from} d={arc(frac(z.from), frac(z.to))} stroke={zoneColour(z)} />
        ))}
      </g>
      {needle && (
        <>
          <line x1={cx} y1={cy} x2={needle[0]} y2={needle[1]} stroke={d.fill} strokeWidth={2.5} strokeLinecap="round" />
          <circle cx={cx} cy={cy} r={4} fill={d.fill} />
        </>
      )}
      <text className="fb-gauge-tick" x={point(0)[0]} y={98} textAnchor="middle">{tick(d.range[0])}</text>
      <text className="fb-gauge-tick" x={point(1)[0]} y={98} textAnchor="middle">{tick(d.range[1])}</text>
    </svg>
  );
}

/** A horizontal fill over the zone strip; scales to its container's width. */
function Bar(d: Drawing) {
  const h = d.height ?? 14;
  const x = (v: number) => ((v - d.range[0]) / (d.range[1] - d.range[0])) * 100;
  return (
    <div className="fb-gauge-bar">
      <svg className="fb-gauge-svg" width="100%" height={h} viewBox="0 0 100 10" preserveAspectRatio="none" role="img">
        <g className="fb-gauge-zones">
          {d.zones.map((z) => (
            <rect key={z.from} x={x(z.from)} width={x(z.to) - x(z.from)} y={0} height={10} fill={zoneColour(z)} opacity={0.35} />
          ))}
        </g>
        {d.fraction !== null && <rect x={0} y={2.5} width={d.fraction * 100} height={5} fill={d.fill} />}
      </svg>
      <div className="fb-gauge-ticks">
        <span>{tick(d.range[0])}</span>
        <span>{tick(d.range[1])}</span>
      </div>
    </div>
  );
}
