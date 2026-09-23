import type { Address, ControllerOut, DeviceRunOut, Event, RigClient, SampleOut, Series, Subscription, Value, WaitState, WriteOut } from "@flyball/client";
import { addressOf, deviceOf, setpointOf, signalsOf, isScratch } from "@flyball/client";
import { Ring, type RingView } from "./ring.js";
import { debugCounters } from "./debug.js";

export type StreamStatus = "connecting" | "open" | "closed";

/**
 * The subscription buckets the store fans updates out to. `writes` and
 * `devices` are not sockets of their own any more -- a demand's write
 * record and a device's run both ride on the `samples` socket now -- but
 * they stay separate buckets here, so a `useWriteState`/`useDeviceRun`
 * subscriber still wakes only on the update it asked for.
 */
export type StoreStream = "samples" | "writes" | "controllers" | "devices" | "waits" | "events";
/** The streams that actually open a socket; `writes` and `devices` share `samples`'s. */
export type SocketStream = "samples" | "controllers" | "waits" | "events";
const STREAMS: SocketStream[] = ["samples", "controllers", "waits", "events"];
/**
 * How long a dropped socket is shown as `"reconnecting"` before escalating to
 * `"closed"` (offline). A drop-and-immediate-reopen is normal churn (a
 * session boundary, a brief network blip) and the transport already retries
 * on its own with backoff -- showing it as an outage from the first
 * `onclose` would make a routine reconnect look identical to a genuine one.
 */
const OFFLINE_GRACE_MS = 3000;
const socketOf = (stream: StoreStream): SocketStream => (stream === "writes" || stream === "devices" ? "samples" : stream);

/** Plain arrays a chart reuses between refreshes: time in seconds since the epoch, value. */
export interface TraceView {
  t: number[];
  v: number[];
}

/** A controller's ticks as parallel arrays; null where it had no value (see `ControllerTrace`). */
export interface ControllerView {
  t: number[];
  reference: (number | null)[];
  measured: (number | null)[];
  output: (number | null)[];
  expected: (number | null)[];
  correction: (number | null)[];
}

export const emptyTrace = (): TraceView => ({ t: [], v: [] });
export const emptyControllerView = (): ControllerView => ({ t: [], reference: [], measured: [], output: [], expected: [], correction: [] });

/**
 * A controller's setpoint, as a synthetic key `read`/`subscribeTrace` accept
 * alongside real signal addresses (Graph's series picker and the telemetry
 * store's `read`/`subscribeTrace` are the only callers): distinct from any
 * address, since an address never contains `:` (`addressOf` joins node and
 * name with `.`). `controllerNameFromSetpointKey` recovers the controller's
 * name (its target address) from one, or null for a plain signal address.
 */
const CONTROLLER_SETPOINT_PREFIX = "controller-setpoint:";
export const controllerSetpointKey = (name: Address): string => `${CONTROLLER_SETPOINT_PREFIX}${name}`;
export const controllerNameFromSetpointKey = (key: string): Address | null => (key.startsWith(CONTROLLER_SETPOINT_PREFIX) ? key.slice(CONTROLLER_SETPOINT_PREFIX.length) : null);

export interface ReadOptions {
  /** Rows from this time (seconds since the epoch) on; everything held when omitted. */
  fromS?: number;
  /** One row in `every`; the newest is always kept. */
  every?: number;
  /** At most this many rows: `every` rises to fit. */
  maxPoints?: number;
  /** Break the line across raw gaps wider than this (seconds); no breaking when omitted. */
  maxGapS?: number;
  /**
   * The chart's own visible span (seconds), used to size the thinning bucket
   * width instead of the ring's full retained `windowS`. Without it, a chart
   * showing a narrower window than the store keeps (e.g. a 60s sparkline fed
   * by a 3600s ring) buckets by the full retained span and drops most of the
   * points actually on screen.
   */
  spanS?: number;
}

export interface TelemetryStoreOptions {
  /** Seconds of history kept per signal and controller. */
  windowS?: number;
  /** Rows per ring at most. */
  capacity?: number;
  /** Seconds a stream's socket stays open after its last subscriber leaves. */
  graceMs?: number;
}

interface Sub {
  /** Keys this subscriber wants, or null for any. */
  keys: Set<string> | null;
  cb: () => void;
  everyMs: number;
  lastAt: number;
  dirty: boolean;
}

const CONTROLLER_COLS = 5; // reference, reading, demand, expected, correction

/** A controller's identity across sessions and restarts: what it drives and what it reads. */
const pairOf = (name: string, measured: string) => `${name}|${measured}`;

const nan = (x: number | null | undefined) => (x == null ? Number.NaN : x);

/** A new ring holding `ring`'s rows with `fromS <= t <= toS`: a playback window cut from the live ring. */
function sliceRing(ring: Ring, fromS: number, toS: number, cap: number): Ring {
  const start = ring.indexAtOrAfter(fromS);
  let end = ring.indexAtOrAfter(toS);
  while (end < ring.length && ring.timeAt(end) <= toS) end++;
  const out = new Ring({ width: ring.width, initial: Math.max(16, end - start), cap });
  const row = new Array<number>(ring.width);
  for (let i = start; i < end; i++) {
    for (let c = 0; c < ring.width; c++) row[c] = ring.valueAt(i, c);
    out.push(ring.timeAt(i), row);
  }
  return out;
}

/** Points a history request asks for: twice the plot width, at most 4 000. */
export const historyPoints = (): number => Math.min(4000, 2 * Math.max(300, typeof window === "undefined" ? 1440 : window.innerWidth));

/** How long a seek waits for the next one before asking the recording store: a slider drag is many seeks a second. */
export const PLAYBACK_DEBOUNCE_MS = 150;
/** Seconds fetched before the window a paused chart shows, so its left edge is not the first point held. */
export const PLAYBACK_MARGIN_S = 60;

export interface PlaybackSession {
  /** The session the history is read from (the open one, `GET /api/recording`). */
  id: number;
  /** Its start, in seconds since the epoch on the rig's clock: history routes take offsets from it. */
  startS: number;
  /** Seconds of history a panel shows ending at `atS`: the widest chart window on the page. */
  windowS: number;
}

/**
 * Every live value the rig publishes, held once and fanned out, keyed by
 * address: samples as a ring buffer per signal (`/ws/samples`, each node's
 * sample expanded to `node.name` addresses), the latest write state per
 * writable signal (a demand's `writes` entry, riding with its reading in
 * the same `/ws/samples` frame), the latest state and a ring of ticks per
 * controller (`/ws/controllers`, keyed by target address), each polled
 * device's run (`/ws/samples`'s `runs`), the waits (`/ws/waits`), a capped
 * ring of events. Subscribers are told once per animation frame at most,
 * each at its own cadence, and read what they need from the rings (no
 * arrays are built per message). A socket opens on the first subscriber to
 * whatever it carries and closes a few seconds after the last one leaves,
 * so a Strict Mode double mount opens each once.
 *
 * `playback(atS, session)` shows the rig as it was at `atS`: every reader
 * of samples (`read`, `latest`, `latestValue`, `count`, `readController`,
 * a controller's `reading`) answers from a window of history ending there
 * instead of the live rings, and live samples stop waking subscribers until
 * `playback(null)`. The live rings keep filling underneath, so resuming is
 * one wake-up with no gap and no request.
 */
export class TelemetryStore {
  readonly windowS: number;
  private readonly capacity: number;
  private readonly graceMs: number;

  private signals = new Map<Address, Ring>();
  private signalVersions = new Map<Address, number>();
  /** A signal's newest value whatever its dtype (a number is also in its `Ring`; a bool/str/json only lives here). */
  private latestValues: Record<Address, { t: number; value: Value }> = {};
  private nodeLatest: Record<Address, SampleOut> = {};
  private nodeVersions = new Map<Address, number>();
  private writeList: Record<Address, WriteOut> = {};
  private writeVersions = new Map<Address, number>();
  private writesVersion = 0;
  private controllerRings = new Map<Address, Ring>();
  /** Scratch `readController` output per controller, reused so reading a setpoint trace allocates nothing new. */
  private controllerSetpointScratch = new Map<Address, ControllerView>();
  private controllerLatest: Record<Address, ControllerOut> = {};
  private controllerVersions = new Map<Address, number>();
  private controllersVersion = 0;
  private deviceRunList: Record<string, DeviceRunOut> = {};
  private deviceVersions = new Map<string, number>();
  private devicesVersion = 0;
  private periodsKey = "";
  private waitList: Record<string, WaitState> = {};
  private waitsVersion = 0;
  private eventList: Event[] = [];
  private eventsVersion = 0;
  private readonly eventCap = 2000;
  private eventsSeeded: Promise<void> | null = null;
  private eventsSeedLimit = 0;

  private subs = { samples: new Set<Sub>(), writes: new Set<Sub>(), controllers: new Set<Sub>(), devices: new Set<Sub>(), waits: new Set<Sub>(), events: new Set<Sub>(), status: new Set<Sub>() };
  /** Subscribers by key, so a sample touches only those that asked for its address; and those that asked for any. */
  private byKey = new Map<string, Set<Sub>>();
  private anyKey = { samples: new Set<Sub>(), writes: new Set<Sub>(), controllers: new Set<Sub>(), devices: new Set<Sub>(), waits: new Set<Sub>(), events: new Set<Sub>(), status: new Set<Sub>() };
  private dirty = new Set<Sub>();
  private frame: number | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  private sockets = new Map<
    SocketStream,
    { subscription: Subscription; count: number; closer: ReturnType<typeof setTimeout> | null; offlineTimer: ReturnType<typeof setTimeout> | null }
  >();
  private statuses: Record<SocketStream, StreamStatus | "idle"> = { samples: "idle", controllers: "idle", waits: "idle", events: "idle" };
  private statusVersion = 0;

  private seededSignals = new Set<Address>();
  private pendingSeed: Address[] = [];
  private seeding: Promise<void> | null = null;
  private newestS = Number.NEGATIVE_INFINITY;
  private seededControllers = new Set<string>();

  /** Where playback is reading from, in rig seconds; null while live. */
  private atS: number | null = null;
  private playbackSession: PlaybackSession | null = null;
  /** Bumped by every seek and by resume: a fetch that comes back for an older one is dropped. */
  private playbackGen = 0;
  /** The window of history per signal and per controller, ending at `atS`. */
  private playbackRings = new Map<Address, Ring>();
  /** The last bool/str/json value at or before `atS` per signal (a number goes on its playback ring). */
  private playbackValues = new Map<Address, { t: number; value: Value }>();
  private playbackTicks = new Map<Address, Ring>();
  /** Signals and controllers whose window must come from the recording store, waiting for the debounce. */
  private playbackPending = new Set<Address>();
  private playbackPendingTicks = new Set<Address>();
  private playbackTimer: ReturnType<typeof setTimeout> | null = null;
  /** What the playback session declared, asked once per session: anything else is a 409 the console would log. */
  private playbackDeclared: { id: number; signals: Promise<Set<Address>>; controllers: Promise<Set<Address>> } | null = null;
  /** A controller's live state with its `reading` swapped for the source's point at `atS`; rebuilt only when either changes. */
  private playbackControllers = new Map<Address, { live: ControllerOut; t: number | undefined; v: number | undefined; out: ControllerOut }>();

  constructor(
    private readonly rig: RigClient,
    { windowS = 3600, capacity = 65536, graceMs = 5000 }: TelemetryStoreOptions = {},
  ) {
    this.windowS = windowS;
    this.capacity = capacity;
    this.graceMs = graceMs;
  }

  // region Signals

  private ring(address: Address): Ring {
    let ring = this.signals.get(address);
    if (!ring) {
      ring = new Ring({ width: 1, initial: 1024, cap: this.capacity, windowS: this.windowS });
      this.signals.set(address, ring);
    }
    return ring;
  }

  /** Every signal address a sample has arrived for. */
  signalKeys(): Address[] {
    return [...this.signals.keys()];
  }

  /**
   * The newest point of a signal, numeric dtypes only (fed the ring); undefined
   * for a signal never sampled or never numeric. In playback: its last point at
   * or before `atS`, or undefined when the window holds none.
   */
  latest(address: Address): { t: number; v: number } | undefined {
    const ring = this.atS === null ? this.signals.get(address) : this.playbackRings.get(address);
    if (!ring || !ring.length) return undefined;
    return { t: ring.lastT()!, v: ring.last()! };
  }

  /**
   * The newest value of a signal whatever its dtype: a number, a bool, a
   * string (an enum) or JSON. In playback: the last value at or before
   * `atS` -- a number from its window, anything else from the session's
   * series (the recording store keeps every dtype).
   */
  latestValue(address: Address): { t: number; value: Value } | undefined {
    if (this.atS === null) return this.latestValues[address];
    const held = this.playbackValues.get(address);
    if (held) return held;
    const point = this.latest(address);
    return point && { t: point.t, value: point.v };
  }

  /**
   * When the signal last delivered, whatever its dtype (a number's ring, or a
   * bool/str/json's latest value): what freshness ages. In playback, a
   * number's last point at or before `atS`; a bool/str/json has no age there
   * -- the session keeps some of those only when they change (a device's
   * mode: one row, then nothing), so the row's time says when it changed,
   * not when it was last read, and ageing it would call a live mode stale.
   */
  lastSampleS(address: Address): number | undefined {
    if (this.atS === null) return this.latestValues[address]?.t ?? this.latest(address)?.t;
    return this.latest(address)?.t;
  }

  /** Bumps whenever the signal gains a point (or its history lands), and on every seek and resume. */
  version(address: Address): number {
    return this.signalVersions.get(address) ?? 0;
  }

  /**
   * Copy a signal's rows into `out` (arrays reused), thinned as asked; the
   * playback window instead while paused. `address` may instead be a
   * `controllerSetpointKey`, read from the controller's ring (its
   * `setpointOf` column) rather than a signal's -- so a chart fed by
   * `source` (`MultiSeries`/`useTraceRef`) can plot a controller's setpoint
   * exactly as it plots a signal, with no `TraceView` of its own.
   */
  read(address: Address, out: TraceView, options: ReadOptions = {}): TraceView {
    const controllerName = controllerNameFromSetpointKey(address);
    if (controllerName !== null) return this.readControllerSetpointTrace(controllerName, out, options);
    const ring = this.atS === null ? this.signals.get(address) : this.playbackRings.get(address);
    if (!ring) {
      out.t.length = 0;
      out.v.length = 0;
      return out;
    }
    const view: RingView = { t: out.t, cols: [out.v] };
    ring.read(view, options);
    return out;
  }

  /** `read`'s controller-setpoint branch: the reference column of `readController`, as a plain `TraceView` (a gap is `NaN`, the same sentinel a real gap-break already uses -- `MultiSeries` draws either as a break). */
  private readControllerSetpointTrace(name: Address, out: TraceView, options: ReadOptions): TraceView {
    let scratch = this.controllerSetpointScratch.get(name);
    if (!scratch) {
      scratch = emptyControllerView();
      this.controllerSetpointScratch.set(name, scratch);
    }
    this.readController(name, scratch, options);
    out.t.length = scratch.t.length;
    out.v.length = scratch.reference.length;
    for (let i = 0; i < scratch.t.length; i++) {
      out.t[i] = scratch.t[i]!;
      const v = scratch.reference[i]!;
      out.v[i] = v === null ? Number.NaN : v;
    }
    return out;
  }

  /** The rig's clock as far as the samples say: the newest sample time across every signal, or null before any. Live even in playback. */
  nowS(): number | null {
    return Number.isFinite(this.newestS) ? this.newestS : null;
  }

  /** The clock a sample is aged against: `atS` in playback (a point just before it is fresh), `nowS()` otherwise. */
  clockS(): number | null {
    return this.atS ?? this.nowS();
  }

  /**
   * The oldest sample time across every signal a page has loaded so far, or
   * null before any has -- what a chart's default window fits itself to
   * (`App`'s `windowS`): a signal seeded from a session with hours of
   * history reaches back further than one just opened, and this is the
   * earliest of whatever has actually landed, live rings only (playback
   * has its own fixed window already).
   */
  earliestS(): number | null {
    let earliest = Number.POSITIVE_INFINITY;
    for (const ring of this.signals.values()) {
      const first = ring.firstT();
      if (first !== undefined) earliest = Math.min(earliest, first);
    }
    return Number.isFinite(earliest) ? earliest : null;
  }

  /** Points held for a signal (in the playback window while paused). */
  count(address: Address): number {
    return (this.atS === null ? this.signals.get(address) : this.playbackRings.get(address))?.length ?? 0;
  }

  /** The newest sample of a node (`values` keyed relative to it); the same object until the node delivers again. */
  sample(node: Address): SampleOut | undefined {
    return this.nodeLatest[node];
  }

  /** Bumps whenever the node delivers a sample. */
  sampleVersion(node: Address): number {
    return this.nodeVersions.get(node) ?? 0;
  }

  /**
   * The poll period of the device an address is under, from `/ws/samples`'s
   * `runs`; undefined until the device has reported, or for one that is not polled.
   * The input to a stale threshold (`staleAfterS`).
   */
  periodOf(address: Address): number | null | undefined {
    return this.deviceRunList[deviceOf(address)]?.period_s;
  }

  /** A live point landed: the version moves always, the subscribers only while live (a paused panel shows the past). */
  private bumpSignal(address: Address): void {
    this.signalVersions.set(address, (this.signalVersions.get(address) ?? 0) + 1);
    if (this.atS === null) this.mark("samples", address);
  }

  private onSamples(samples: SampleOut[]): void {
    if (!samples.length) return;
    const row = [0];
    const next = { ...this.nodeLatest };
    let nextWrites: Record<Address, WriteOut> | null = null;
    for (const sample of samples) {
      const time = sample.time_ns / 1e9;
      if (time > this.newestS) this.newestS = time;
      for (const name in sample.values) {
        const address = addressOf(sample.node, name);
        const value = sample.values[name]!;
        this.latestValues[address] = { t: time, value };
        // Only a number feeds a ring (charts, sparklines); a bool/str/json is kept as a latest value only.
        if (typeof value === "number") {
          row[0] = value;
          this.ring(address).push(time, row);
        }
        this.bumpSignal(address);
      }
      // A demand's write record: `sample.values` already has the committed value, so it is
      // joined back in here, into the same `WriteOut` shape `/ws/writes` used to send.
      for (const name in sample.writes) {
        const address = addressOf(sample.node, name);
        const meta = sample.writes[name]!;
        const value = sample.values[name];
        nextWrites ??= { ...this.writeList };
        nextWrites[address] = { value: typeof value === "number" ? value : null, ...meta };
        this.writeVersions.set(address, (this.writeVersions.get(address) ?? 0) + 1);
        this.mark("writes", address);
      }
      next[sample.node] = sample;
      this.nodeVersions.set(sample.node, (this.nodeVersions.get(sample.node) ?? 0) + 1);
      this.mark("samples", sample.node);
    }
    this.nodeLatest = next;
    if (nextWrites) {
      this.writeList = nextWrites;
      this.writesVersion++;
    }
  }

  /**
   * Called once per animation frame for any of `addresses` that gained
   * points (`everyMs` apart at least). An address may be a
   * `controllerSetpointKey`: it wakes on that controller's tick (the
   * `controllers` stream), not on a sample.
   */
  subscribeTrace(addresses: Address[], cb: () => void, everyMs = 100): () => void {
    const signals: Address[] = [];
    const controllers: Address[] = [];
    for (const a of addresses) {
      const name = controllerNameFromSetpointKey(a);
      if (name !== null) controllers.push(name);
      else signals.push(a);
    }
    const unsubs: Array<() => void> = [];
    if (signals.length || !controllers.length) unsubs.push(this.subscribe("samples", new Set(signals), cb, everyMs));
    if (controllers.length) unsubs.push(this.subscribe("controllers", new Set(controllers), cb, everyMs));
    return () => unsubs.forEach((u) => u());
  }

  /** As `subscribeTrace` for one signal, at a readout's cadence. */
  subscribeLatest(address: Address, cb: () => void, everyMs = 250): () => void {
    return this.subscribe("samples", new Set([address]), cb, everyMs);
  }

  /** Called when the node delivers a sample, at most every `everyMs`. */
  subscribeSample(node: Address, cb: () => void, everyMs = 250): () => void {
    return this.subscribe("samples", new Set([node]), cb, everyMs);
  }

  /**
   * Fill the window from the recording store: one `/series` per signal per
   * session, newest session first until the window is covered, at most 20
   * sessions. Once per signal per page life; points that arrived live
   * before the history landed keep their place after it.
   */
  seed(addresses: Address[]): Promise<void> {
    // A controller's setpoint seeds itself (`seedControllers`, keyed on the controller, not a signal);
    // skip its synthetic key here rather than fetch `/series` for an address that does not exist.
    addresses = addresses.filter((a) => controllerNameFromSetpointKey(a) === null);
    if (this.atS !== null) this.ensurePlayback(addresses);
    const wanted = addresses.filter((a) => !this.seededSignals.has(a));
    if (!wanted.length) return this.seeding ?? Promise.resolve();
    for (const a of wanted) this.seededSignals.add(a);
    // Forty tiles mounting together ask once: the addresses are gathered for a tick and fetched as one batch.
    this.pendingSeed.push(...wanted);
    this.seeding ??= new Promise<void>((resolve) => {
      setTimeout(() => {
        const batch = this.pendingSeed;
        this.pendingSeed = [];
        this.seeding = null;
        Promise.all([
          this.fetchHistory(batch).catch(() => undefined), // no store: live-only is fine
          this.fetchLatest(batch).catch(() => undefined),
        ]).then(() => resolve());
      }, 0);
    });
    return this.seeding;
  }

  /**
   * What each signal reads now, from the rig's own latest: a mode that was
   * set before this page opened and has not changed since never arrives on
   * the stream, and with no session recorded there is no history to seed it
   * from either. One `GET /api/devices` carries every signal's `latest`,
   * so nothing is asked of a signal that has never been read (a demand not
   * yet set, a device's housekeeping): no 503 for the console. Only fills a
   * signal nothing else has given a value to.
   */
  private async fetchLatest(addresses: Address[]): Promise<void> {
    const wanted = new Set(addresses);
    const devices = await this.rig.devices();
    let changed = false;
    for (const signal of devices.flatMap((d) => signalsOf(d.signals))) {
      if (!wanted.has(signal.address) || !signal.latest || signal.latest.value === null || this.latestValues[signal.address]) continue;
      const time = signal.latest.time_ns / 1e9;
      const value = signal.latest.value;
      this.latestValues[signal.address] = { t: time, value };
      if (typeof value === "number" && this.ring(signal.address).length === 0) this.ring(signal.address).push(time, [value]);
      this.bumpSignal(signal.address);
      changed = true;
    }
    if (changed) this.flushSoon();
  }

  private async fetchHistory(addresses: Address[]): Promise<void> {
    const rig = this.rig;
    const [sessions, clock, current] = await Promise.all([rig.sessions(20), rig.clock(), rig.recording().catch(() => null)]);
    const nowS = clock.now_ns / 1e9;
    const horizonS = nowS - this.windowS;
    const parts = new Map<Address, Array<{ t: number[]; v: number[] }>>();
    let floorS = Number.POSITIVE_INFINITY; // sessions must not overlap on the axis (see `seedControllers`)
    const maxPoints = historyPoints();
    for (const session of sessions) {
      // Only the current recording, or the runner's rolling scratch record, may be open; another
      // open session was left by a runner that died and would overlay stale readings on the live trace.
      if (session.end_ns === null && session.id !== current?.id && !isScratch(session)) continue;
      const startS = session.start_ns / 1e9;
      const endS = session.end_ns === null ? nowS : session.end_ns / 1e9;
      if (endS > floorS) continue;
      floorS = startS;
      if (endS < horizonS) break;
      const startOffset = Math.max(0, Math.floor((horizonS - startS) * 1e9));
      // Only what the session declared: asking for anything else is a 409 the console would log for every signal.
      const declared = new Set((await rig.sessionSignals(session.id).catch(() => [])).map((row) => row.address));
      await Promise.all(
        addresses.filter((address) => declared.has(address)).map(async (address) => {
          const series = await rig.series(session.id, address, { start_ns: startOffset, max_points: maxPoints }).catch(() => null);
          // Only a number belongs on a ring: the store records a bool/str/json signal too, and those used to land as NaN rows.
          const points = series?.points.filter((p) => typeof p.value === "number") ?? [];
          if (!points.length) return;
          (parts.get(address) ?? parts.set(address, []).get(address)!).push({
            t: points.map((p) => startS + p.offset_ns / 1e9),
            v: points.map((p) => p.value),
          });
        }),
      );
    }
    for (const [address, chunks] of parts) {
      chunks.sort((a, b) => a.t[0]! - b.t[0]!);
      const t = chunks.flatMap((k) => k.t);
      const v = chunks.flatMap((k) => k.v);
      this.ring(address).prepend(t, [v]);
      this.bumpSignal(address);
    }
    this.flushSoon();
  }

  // endregion

  // region Writes

  /** Every writable signal's latest write state, by address; the same object until one changes. */
  writes(): Record<Address, WriteOut> {
    return this.writeList;
  }

  write(address: Address): WriteOut | undefined {
    return this.writeList[address];
  }

  /** Bumps when the signal is committed, or any signal when `address` is omitted. */
  writeVersion(address?: Address): number {
    return address === undefined ? this.writesVersion : (this.writeVersions.get(address) ?? 0);
  }

  /** Called when the signal is committed (any signal when `address` is null), at most every `everyMs`. */
  subscribeWrites(address: Address | null, cb: () => void, everyMs = 250): () => void {
    return this.subscribe("writes", address === null ? null : new Set([address]), cb, everyMs);
  }

  // endregion

  // region Controllers

  private controllerRing(name: Address): Ring {
    let ring = this.controllerRings.get(name);
    if (!ring) {
      ring = new Ring({ width: CONTROLLER_COLS, initial: 256, cap: this.capacity, windowS: this.windowS });
      this.controllerRings.set(name, ring);
    }
    return ring;
  }

  /** The latest `ControllerOut` per name (target address); the same object until a controller changes. */
  controllers(): Record<Address, ControllerOut> {
    return this.controllerLatest;
  }

  /**
   * One controller's latest state. In playback its mode, reference, output
   * and law stay live (commanded values are not samples) but `measured` is
   * the measured signal's point at `atS`, so a faceplate's PV and its trend agree.
   */
  controller(name: Address): ControllerOut | undefined {
    const live = this.controllerLatest[name];
    if (this.atS === null || !live) return live;
    const point = this.latest(live.measured_signal);
    const held = this.playbackControllers.get(name);
    if (held && held.live === live && held.t === point?.t && held.v === point?.v) return held.out;
    const out: ControllerOut = { ...live, measured: point ? { signal: live.measured_signal, time_ns: Math.round(point.t * 1e9), value: point.v } : null };
    this.playbackControllers.set(name, { live, t: point?.t, v: point?.v, out });
    return out;
  }

  /** Bumps when the named controller ticks, or any controller when `name` is omitted. */
  controllerVersion(name?: Address): number {
    return name === undefined ? this.controllersVersion : (this.controllerVersions.get(name) ?? 0);
  }

  /** Copy a controller's ticks into `out` (arrays reused), thinned as asked; the playback window instead while paused. */
  readController(name: Address, out: ControllerView, options: ReadOptions = {}): ControllerView {
    const ring = this.atS === null ? this.controllerRings.get(name) : this.playbackTicks.get(name);
    const cols = [out.reference, out.measured, out.output, out.expected, out.correction] as number[][];
    if (!ring) {
      out.t.length = 0;
      for (const c of cols) c.length = 0;
      return out;
    }
    ring.read({ t: out.t, cols }, options);
    for (const c of cols) for (let i = 0; i < c.length; i++) if (Number.isNaN(c[i])) (c as (number | null)[])[i] = null;
    return out;
  }

  private onControllers(controllers: ControllerOut[]): void {
    if (!controllers.length) return;
    const next = { ...this.controllerLatest };
    const row = new Array<number>(CONTROLLER_COLS);
    for (const c of controllers) {
      next[c.name] = c;
      const time = c.measured ? c.measured.time_ns / 1e9 : Date.now() / 1000;
      row[0] = nan(setpointOf(c));
      row[1] = nan(c.measured && typeof c.measured.value === "number" ? c.measured.value : null);
      row[2] = nan(c.output);
      row[3] = nan(c.expected);
      row[4] = nan(c.correction);
      this.controllerRing(c.name).push(time, row);
      this.controllerVersions.set(c.name, (this.controllerVersions.get(c.name) ?? 0) + 1);
      this.mark("controllers", c.name);
    }
    this.controllerLatest = next;
    this.controllersVersion++;
  }

  /** Called when the named controller ticks (any controller when `name` is null), at most every `everyMs`. */
  subscribeController(name: Address | null, cb: () => void, everyMs = 250): () => void {
    return this.subscribe("controllers", name === null ? null : new Set([name]), cb, everyMs);
  }

  /**
   * `GET /api/controllers` once (so the latest state is there before the
   * socket's first message) and the window of ticks from the recording
   * store for each controller not yet seeded, matched by (output, measured signal)
   * across sessions.
   */
  seedControllers(every?: number): Promise<void> {
    return this.rig
      .controllers()
      .then(async (controllers) => {
        const fresh = controllers.filter((c) => !(c.name in this.controllerLatest));
        if (fresh.length) this.onControllers(fresh);
        const wanted = controllers.filter((c) => !this.seededControllers.has(pairOf(c.name, c.measured_signal)));
        if (!wanted.length) return;
        for (const c of wanted) this.seededControllers.add(pairOf(c.name, c.measured_signal));
        const traces = await this.fetchTicks(wanted, every);
        for (const [name, view] of traces) {
          this.controllerRing(name).prepend(view.t, [view.reference, view.measured, view.output, view.expected, view.correction].map((c) => c.map(nan)));
          this.controllerVersions.set(name, (this.controllerVersions.get(name) ?? 0) + 1);
          this.mark("controllers", name);
        }
        this.controllersVersion++;
        this.flushSoon();
      })
      .catch(() => undefined); // the socket sends every controller on connect anyway; no store is fine
  }

  private async fetchTicks(controllers: ControllerOut[], every?: number): Promise<Map<Address, ControllerView>> {
    const rig = this.rig;
    const out = new Map<Address, ControllerView>();
    const [sessions, clock, current] = await Promise.all([rig.sessions(20).catch(() => []), rig.clock(), rig.recording().catch(() => null)]);
    if (!sessions.length) return out;
    const nowS = clock.now_ns / 1e9; // the rig's now: a simulated clock runs ahead of the wall
    const wanted = new Map(controllers.map((c) => [pairOf(c.name, c.measured_signal), c.name]));
    const perController = new Map<Address, Array<{ startS: number; ticks: Awaited<ReturnType<RigClient["ticks"]>> }>>();
    const horizonS = nowS - this.windowS;
    // Sessions must sit one after another on the time axis. A simulated clock
    // restarts with the runner, so an older session can carry *later* stamps
    // than the current one; such a session cannot share the axis and is skipped.
    let floorS = Number.POSITIVE_INFINITY;
    for (const session of sessions) {
      if (session.end_ns === null && session.id !== current?.id && !isScratch(session)) continue; // an orphan
      const startS = session.start_ns / 1e9;
      const endS = session.end_ns === null ? nowS : session.end_ns / 1e9;
      if (endS > floorS) continue;
      floorS = startS;
      if (endS < horizonS) break;
      const rows = await rig.sessionControllers(session.id).catch(() => []);
      await Promise.all(
        rows.map(async (row) => {
          const name = wanted.get(pairOf(row.name, row.measured));
          if (!name) return;
          const startOffset = Math.max(0, (horizonS - startS) * 1e9); // history routes take offsets from the session's start
          const ticks = await rig.ticks(session.id, row.name, { start_ns: Math.floor(startOffset), ...(every && every > 1 ? { every } : {}) }).catch(() => []);
          if (ticks.length) (perController.get(name) ?? perController.set(name, []).get(name)!).push({ startS, ticks });
        }),
      );
    }
    for (const [name, parts] of perController) {
      parts.sort((a, b) => a.startS - b.startS);
      const view = emptyControllerView();
      for (const { startS, ticks } of parts) {
        for (const k of ticks) {
          view.t.push(startS + k.offset_ns / 1e9);
          view.reference.push(k.setpoint);
          view.measured.push(k.measured);
          view.output.push(k.output);
          view.expected.push(k.expected ?? null);
          view.correction.push(k.correction ?? null);
        }
      }
      out.set(name, view);
    }
    return out;
  }

  // endregion

  // region Devices

  /** Every polled device's run (period, running, last read, conditions, state) by name; the same object until one changes. */
  deviceRuns(): Record<string, DeviceRunOut> {
    return this.deviceRunList;
  }

  deviceRun(name: string): DeviceRunOut | undefined {
    return this.deviceRunList[name];
  }

  /** Bumps when the named device reports, or any device when `name` is omitted. */
  deviceVersion(name?: string): number {
    return name === undefined ? this.devicesVersion : (this.deviceVersions.get(name) ?? 0);
  }

  /** The devices' periods as one string; changes only when a period does (a stale threshold's input). */
  devicePeriodsKey(): string {
    return this.periodsKey;
  }

  private onDevices(devices: DeviceRunOut[]): void {
    if (!devices.length) return;
    const next = { ...this.deviceRunList };
    for (const run of devices) {
      next[run.name] = run;
      this.deviceVersions.set(run.name, (this.deviceVersions.get(run.name) ?? 0) + 1);
      this.mark("devices", run.name);
    }
    this.deviceRunList = next;
    this.devicesVersion++;
    this.periodsKey = Object.keys(next)
      .sort()
      .map((name) => `${name}=${next[name]!.period_s ?? ""}`)
      .join(",");
  }

  /** Called when the named device reports (any device when `name` is null), at most every `everyMs` (a second: a run is a footer line). */
  subscribeDevices(name: string | null, cb: () => void, everyMs = 1000): () => void {
    return this.subscribe("devices", name === null ? null : new Set([name]), cb, everyMs);
  }

  // endregion

  // region Waits

  /** Every wait the socket has reported, by name, settled ones included; the same object until one changes. */
  waits(): Record<string, WaitState> {
    return this.waitList;
  }

  waitsVersionNow(): number {
    return this.waitsVersion;
  }

  private onWaits(waits: WaitState[]): void {
    if (!waits.length) return;
    const next = { ...this.waitList };
    for (const wait of waits) next[wait.name] = wait;
    this.waitList = next;
    this.waitsVersion++;
    this.mark("waits", null);
  }

  subscribeWaits(cb: () => void, everyMs = 250): () => void {
    return this.subscribe("waits", null, cb, everyMs);
  }

  // endregion

  // region Events

  /** The events held, oldest first; the same array until one arrives. */
  events(): Event[] {
    return this.eventList;
  }

  eventsVersionNow(): number {
    return this.eventsVersion;
  }

  private onEvents(events: Event[]): void {
    if (!events.length) return;
    // The socket resends recent events on connect: drop what is at or before the newest held.
    const last = this.eventList.length ? this.eventList[this.eventList.length - 1]!.time_ns : Number.NEGATIVE_INFINITY;
    const fresh = events.filter((e) => e.time_ns > last);
    if (!fresh.length) return;
    let next = [...this.eventList, ...fresh];
    if (next.length > this.eventCap) next = next.slice(next.length - this.eventCap);
    this.eventList = next;
    this.eventsVersion++;
    this.mark("events", null);
  }

  subscribeEvents(cb: () => void, everyMs = 250): () => void {
    return this.subscribe("events", null, cb, everyMs);
  }

  /** `GET /api/events` once, merged under whatever the socket has already delivered. */
  seedEvents(limit = 500): Promise<void> {
    if (this.eventsSeeded && limit <= this.eventsSeedLimit) return this.eventsSeeded;
    this.eventsSeedLimit = limit;
    this.eventsSeeded = this.rig
      .events({ limit })
      .then((events) => {
        const first = this.eventList.length ? this.eventList[0]!.time_ns : Number.POSITIVE_INFINITY;
        const older = events.filter((e) => e.time_ns < first);
        if (!older.length) return;
        let next = [...older, ...this.eventList];
        if (next.length > this.eventCap) next = next.slice(next.length - this.eventCap);
        this.eventList = next;
        this.eventsVersion++;
        this.mark("events", null);
        this.flushSoon();
      })
      .catch(() => undefined);
    return this.eventsSeeded;
  }

  // endregion

  // region Status

  /** Per socket: `idle` until someone subscribes, then its state. `writes`/`devices` share `samples`'s. */
  status(): Readonly<Record<SocketStream, StreamStatus | "idle">> {
    return this.statuses;
  }

  /** Statuses of the streams that have been opened, for a live/reconnecting/offline chip. */
  openStatuses(): StreamStatus[] {
    return STREAMS.map((s) => this.statuses[s]).filter((s): s is StreamStatus => s !== "idle");
  }

  statusVersionNow(): number {
    return this.statusVersion;
  }

  subscribeStatus(cb: () => void): () => void {
    const sub: Sub = { keys: null, cb, everyMs: 0, lastAt: 0, dirty: false };
    this.subs.status.add(sub);
    this.anyKey.status.add(sub);
    return () => {
      this.subs.status.delete(sub);
      this.anyKey.status.delete(sub);
      this.dirty.delete(sub);
    };
  }

  private setStatus(stream: SocketStream, status: StreamStatus | "idle"): void {
    if (this.statuses[stream] === status) return;
    this.statuses = { ...this.statuses, [stream]: status };
    this.statusVersion++;
    this.mark("status", null);
    this.flushSoon();
  }

  // endregion

  // region Playback

  /** Where playback is reading from, in rig seconds; null while live. */
  playbackAtS(): number | null {
    return this.atS;
  }

  /**
   * Show the rig as it was at `atS` (rig seconds), or go back to live with
   * `null`. Every reader of samples then answers from a window of
   * `session.windowS + PLAYBACK_MARGIN_S` seconds ending at `atS`. A signal
   * whose live ring already reaches back that far is served from a copy of
   * it -- no request; the store holds up to an hour -- and the rest come
   * from the session's `/series` (and `/ticks`) after a short debounce, so
   * a slider drag asks once when it settles. Until a fetched window lands
   * the panel keeps the previous one, or shows nothing on the first seek.
   * Resuming drops the windows and wakes every subscriber: the live rings
   * filled all along, so there is no gap and nothing to fetch.
   */
  playback(atS: number | null, session?: PlaybackSession): void {
    if (atS === null) {
      if (this.atS === null) return;
      this.atS = null;
      this.playbackGen++;
      this.clearPlayback();
      this.wakeAll();
      return;
    }
    if (session) this.playbackSession = session;
    if (!this.playbackSession) return; // nothing to read history from yet
    this.atS = atS;
    this.playbackGen++;
    this.playbackControllers.clear();
    // Every window is for the old `atS`: rebuild those the live ring covers now, queue the rest.
    const keys = new Set<Address>([...this.playbackRings.keys(), ...this.playbackPending, ...this.seededSignals]);
    for (const [index, subs] of this.byKey) if (index.startsWith("samples:") && subs.size) keys.add(index.slice("samples:".length));
    this.playbackPending.clear();
    this.playbackPendingTicks.clear();
    this.ensurePlayback([...keys], true);
    for (const name of Object.keys(this.controllerLatest)) this.ensurePlaybackTicks(name, true);
    this.wakeAll();
  }

  private clearPlayback(): void {
    this.playbackRings.clear();
    this.playbackValues.clear();
    this.playbackTicks.clear();
    this.playbackPending.clear();
    this.playbackPendingTicks.clear();
    this.playbackControllers.clear();
    if (this.playbackTimer !== null) clearTimeout(this.playbackTimer);
    this.playbackTimer = null;
  }

  /** Every sample and controller subscriber re-reads: the clock they read against just moved. */
  private wakeAll(): void {
    for (const address of new Set([...this.signals.keys(), ...Object.keys(this.latestValues)])) this.signalVersions.set(address, (this.signalVersions.get(address) ?? 0) + 1);
    this.mark("samples", null);
    for (const name of Object.keys(this.controllerLatest)) this.controllerVersions.set(name, (this.controllerVersions.get(name) ?? 0) + 1);
    this.controllersVersion++;
    this.mark("controllers", null);
    this.flushSoon();
  }

  /** The playback window's bounds, in rig seconds. */
  private playbackWindow(): { fromS: number; toS: number } {
    const toS = this.atS!;
    return { fromS: toS - this.playbackSession!.windowS - PLAYBACK_MARGIN_S, toS };
  }

  /**
   * Give each address a window: a copy of the live ring where it reaches back
   * to the window's start (or to the session's start), else a fetch. With
   * `rebuild` an existing window is replaced; without, only a missing one is made.
   */
  private ensurePlayback(addresses: Address[], rebuild = false): void {
    const { fromS, toS } = this.playbackWindow();
    let queued = false;
    for (const address of addresses) {
      if (!rebuild && (this.playbackRings.has(address) || this.playbackPending.has(address))) continue;
      const live = this.signals.get(address);
      const first = live?.firstT();
      if (live && first !== undefined && (first <= fromS || first <= this.playbackSession!.startS + 1)) {
        this.playbackRings.set(address, sliceRing(live, fromS, toS, this.capacity));
        this.playbackValues.delete(address);
      } else {
        this.playbackPending.add(address);
        queued = true;
      }
    }
    if (queued) this.scheduleFetch();
  }

  private ensurePlaybackTicks(name: Address, rebuild = false): void {
    if (!rebuild && (this.playbackTicks.has(name) || this.playbackPendingTicks.has(name))) return;
    const { fromS, toS } = this.playbackWindow();
    const live = this.controllerRings.get(name);
    const first = live?.firstT();
    if (live && first !== undefined && (first <= fromS || first <= this.playbackSession!.startS + 1)) {
      this.playbackTicks.set(name, sliceRing(live, fromS, toS, this.capacity));
    } else {
      this.playbackPendingTicks.add(name);
      this.scheduleFetch();
    }
  }

  private scheduleFetch(): void {
    if (this.playbackTimer !== null) clearTimeout(this.playbackTimer);
    this.playbackTimer = setTimeout(() => {
      this.playbackTimer = null;
      void this.fetchPlayback();
    }, PLAYBACK_DEBOUNCE_MS);
  }

  private declared(session: PlaybackSession): { signals: Promise<Set<Address>>; controllers: Promise<Set<Address>> } {
    if (this.playbackDeclared?.id !== session.id) {
      this.playbackDeclared = {
        id: session.id,
        signals: this.rig
          .sessionSignals(session.id)
          .then((rows) => new Set(rows.map((r) => r.address)))
          .catch(() => new Set<Address>()),
        controllers: this.rig
          .sessionControllers(session.id)
          .then((rows) => new Set(rows.map((r) => r.name)))
          .catch(() => new Set<Address>()),
      };
    }
    return this.playbackDeclared;
  }

  /** One `/series` per pending signal and one `/ticks` per pending controller, for the window; dropped if playback moved on meanwhile. */
  private async fetchPlayback(): Promise<void> {
    const session = this.playbackSession;
    if (this.atS === null || !session) return;
    const gen = this.playbackGen;
    const { fromS, toS } = this.playbackWindow();
    const addresses = [...this.playbackPending];
    const names = [...this.playbackPendingTicks];
    this.playbackPending.clear();
    this.playbackPendingTicks.clear();
    const declared = this.declared(session);
    // Half-open `[start, end)` offsets from the session's start: one nanosecond past `atS` keeps a point exactly on it.
    const start_ns = Math.max(0, Math.floor((fromS - session.startS) * 1e9));
    const end_ns = Math.floor((toS - session.startS) * 1e9) + 1;
    if (end_ns <= start_ns) return;
    const maxPoints = historyPoints();
    const [signals, controllers] = await Promise.all([declared.signals, declared.controllers]);
    await Promise.all([
      ...addresses.map(async (address) => {
        const ring = new Ring({ width: 1, initial: 64, cap: this.capacity });
        let last: { t: number; value: Value } | undefined;
        if (signals.has(address)) {
          const take = (series: Series | null) => {
            for (const p of series?.points ?? []) {
              const t = session.startS + p.offset_ns / 1e9;
              // The wire says `number`, but the store keeps a bool/str/json signal's values too: those are a latest value, never a ring row.
              const value = p.value as Value;
              if (typeof value === "number") ring.push(t, [value]);
              else if (value !== null) last = { t, value };
            }
            return series;
          };
          const series = take(await this.rig.series(session.id, address, { start_ns, end_ns, max_points: maxPoints }).catch(() => null));
          if (gen !== this.playbackGen) return;
          // A bool/str/json with nothing in the window (a mode the session keeps only when it changes): its last
          // row before the window is still what the signal read at `atS`. Sparse by construction, so unbounded.
          if (series && series.signal.dtype !== "float" && !series.points.length && start_ns > 0) {
            take(await this.rig.series(session.id, address, { start_ns: 0, end_ns: start_ns }).catch(() => null));
            if (gen !== this.playbackGen) return;
          }
        }
        if (gen !== this.playbackGen) return;
        // An undeclared signal gets an empty window: nothing, not the live value.
        this.playbackRings.set(address, ring);
        if (last) this.playbackValues.set(address, last);
        else this.playbackValues.delete(address);
        this.signalVersions.set(address, (this.signalVersions.get(address) ?? 0) + 1);
        this.mark("samples", address);
      }),
      ...names.map(async (name) => {
        const ring = new Ring({ width: CONTROLLER_COLS, initial: 64, cap: this.capacity });
        if (controllers.has(name)) {
          const ticks = await this.rig.ticks(session.id, name, { start_ns, end_ns }).catch(() => []);
          if (gen !== this.playbackGen) return;
          for (const k of ticks) ring.push(session.startS + k.offset_ns / 1e9, [nan(k.setpoint), nan(k.measured), nan(k.output), nan(k.expected), nan(k.correction)]);
        }
        if (gen !== this.playbackGen) return;
        this.playbackTicks.set(name, ring);
        this.controllerVersions.set(name, (this.controllerVersions.get(name) ?? 0) + 1);
        this.mark("controllers", name);
      }),
    ]);
    if (gen === this.playbackGen) {
      this.playbackControllers.clear();
      this.controllersVersion++;
      this.flushSoon();
    }
  }

  // endregion

  // region Fan-out

  private subscribe(stream: StoreStream, keys: Set<string> | null, cb: () => void, everyMs: number): () => void {
    // Subscribers at one cadence start at random phases, so forty readouts do not all
    // re-render in the same frame (one long task) but spread over their period.
    const sub: Sub = { keys, cb, everyMs, lastAt: Date.now() - Math.random() * everyMs, dirty: false };
    this.subs[stream].add(sub);
    const index = (k: string) => `${stream}:${k}`;
    for (const k of keys ?? []) (this.byKey.get(index(k)) ?? this.byKey.set(index(k), new Set()).get(index(k))!).add(sub);
    if (keys === null) this.anyKey[stream].add(sub);
    this.acquire(socketOf(stream));
    // A panel that arrives while paused (a page change) gets the past too, not the live ring.
    if (stream === "samples" && keys && this.atS !== null) this.ensurePlayback([...keys]);
    return () => {
      this.subs[stream].delete(sub);
      this.anyKey[stream].delete(sub);
      for (const k of keys ?? []) {
        const set = this.byKey.get(index(k));
        set?.delete(sub);
        if (set && !set.size) this.byKey.delete(index(k));
      }
      this.dirty.delete(sub);
      this.release(socketOf(stream));
    };
  }

  private mark(stream: StoreStream | "status", key: string | null): void {
    if (key !== null) {
      const keyed = this.byKey.get(`${stream}:${key}`);
      if (keyed) for (const sub of keyed) this.wake(sub);
    }
    if (key === null) for (const sub of this.subs[stream]) this.wake(sub);
    else for (const sub of this.anyKey[stream]) this.wake(sub);
  }

  private wake(sub: Sub): void {
    if (sub.dirty) return;
    sub.dirty = true;
    this.dirty.add(sub);
  }

  /** One flush per animation frame (a timer where there is no frame: a hidden tab, a test). */
  private flushSoon(): void {
    if (this.frame !== null) return;
    if (typeof requestAnimationFrame === "function" && typeof document !== "undefined" && document.visibilityState !== "hidden") {
      this.frame = requestAnimationFrame(() => this.flush());
    } else {
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = setTimeout(() => this.flush(), 16);
    }
  }

  private flush(): void {
    if (this.frame !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(this.frame);
    if (this.timer !== null) clearTimeout(this.timer);
    this.frame = null;
    this.timer = null;
    if (!this.dirty.size) return;
    const now = Date.now();
    let nextDue = Number.POSITIVE_INFINITY;
    for (const sub of [...this.dirty]) {
      const due = sub.lastAt + sub.everyMs;
      if (due <= now) {
        sub.dirty = false;
        sub.lastAt = now;
        this.dirty.delete(sub);
        try {
          sub.cb();
        } catch (error) {
          console.error(error);
        }
      } else if (due < nextDue) nextDue = due;
    }
    // Subscribers not yet due: come back when the nearest is.
    if (this.dirty.size) this.timer = setTimeout(() => this.flush(), Math.max(1, nextDue - now));
  }

  // endregion

  // region Sockets

  private acquire(stream: SocketStream): void {
    const held = this.sockets.get(stream);
    if (held) {
      held.count++;
      if (held.closer !== null) {
        clearTimeout(held.closer);
        held.closer = null;
      }
      return;
    }
    this.setStatus(stream, "connecting");
    const counters = debugCounters();
    const onMessage = (message: unknown) => {
      counters.messages[stream] = (counters.messages[stream] ?? 0) + 1;
      switch (stream) {
        case "samples": {
          // `writes` and `devices` used to be their own frames; a demand's write record and a
          // device's run now ride in this one, under `samples.writes` and the frame's `runs`.
          const frame = message as { samples?: SampleOut[]; runs?: DeviceRunOut[] };
          this.onSamples(frame.samples ?? []);
          if (frame.runs?.length) this.onDevices(frame.runs);
          break;
        }
        case "controllers":
          this.onControllers((message as { controllers: ControllerOut[] }).controllers ?? []);
          break;
        case "waits":
          this.onWaits((message as { waits: WaitState[] }).waits ?? []);
          break;
        case "events":
          this.onEvents((message as { events: Event[] }).events ?? []);
          break;
      }
      this.flushSoon();
    };
    // eslint-disable-next-line prefer-const
    let subscription: Subscription;
    subscription = this.rig.stream(stream, {
      onMessage,
      onOpen: () => {
        const s = this.sockets.get(stream);
        if (s?.offlineTimer !== null && s?.offlineTimer !== undefined) {
          clearTimeout(s.offlineTimer);
          s.offlineTimer = null;
        }
        this.setStatus(stream, "open");
      },
      onClose: () => {
        // A socket this store let go (`release` after the grace) closes asynchronously, after its
        // status went back to idle: that close is not a drop, and must not leave the stream
        // "connecting" for good (the app bar's chip read red on every page without the stream).
        const s = this.sockets.get(stream);
        if (!s || s.subscription !== subscription) return;
        // Shown as "connecting" (reconnecting, amber) immediately -- the transport already has
        // a retry scheduled -- and only escalated to "closed" (offline, red) if it is still down
        // after OFFLINE_GRACE_MS, so a routine drop-and-reopen never reads as an outage.
        this.setStatus(stream, "connecting");
        if (s.offlineTimer !== null) clearTimeout(s.offlineTimer);
        s.offlineTimer = setTimeout(() => {
          const still = this.sockets.get(stream);
          if (still) still.offlineTimer = null;
          if (this.statuses[stream] !== "open") this.setStatus(stream, "closed");
        }, OFFLINE_GRACE_MS);
      },
    });
    this.sockets.set(stream, { subscription, count: 1, closer: null, offlineTimer: null });
  }

  private release(stream: SocketStream): void {
    const held = this.sockets.get(stream);
    if (!held) return;
    held.count--;
    if (held.count > 0 || held.closer !== null) return;
    if (held.offlineTimer !== null) {
      clearTimeout(held.offlineTimer);
      held.offlineTimer = null;
    }
    held.closer = setTimeout(() => {
      this.sockets.delete(stream);
      held.subscription.close();
      this.setStatus(stream, "idle");
    }, this.graceMs);
  }

  /** How many sockets are open now (for tests and the debug counters). */
  openSockets(): number {
    return this.sockets.size;
  }

  /** Close every socket at once; the store is not reusable after. */
  dispose(): void {
    for (const [stream, held] of this.sockets) {
      if (held.closer !== null) clearTimeout(held.closer);
      if (held.offlineTimer !== null) clearTimeout(held.offlineTimer);
      held.subscription.close();
      this.setStatus(stream, "idle");
    }
    this.sockets.clear();
    this.clearPlayback();
    if (this.frame !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(this.frame);
    if (this.timer !== null) clearTimeout(this.timer);
    this.frame = null;
    this.timer = null;
  }

  // endregion
}
