import type { ChannelOut, DeviceState, Event, LoopOut, ReaderRun, RigClient, SampleOut, Subscription } from "@flyball/client";
import { setpointOf } from "@flyball/client";
import { Ring, type RingView } from "./ring.js";
import { debugCounters } from "./debug.js";

export type StreamStatus = "connecting" | "open" | "closed";

/** The streams the store owns (one socket each, shared by every subscriber); `signals` stays with `useStream`. */
export type StoreStream = "samples" | "loops" | "actuators" | "events" | "readers";
const STREAMS: StoreStream[] = ["samples", "loops", "actuators", "events", "readers"];

export const channelKey = (c: { source: string; measurand: string }) => `${c.source}.${c.measurand}`;

/** Plain arrays a chart reuses between refreshes: time in seconds since the epoch, value. */
export interface TraceView {
  t: number[];
  v: number[];
}

/** A loop's ticks as parallel arrays; null where the loop had no value (see `LoopTrace`). */
export interface LoopView {
  t: number[];
  reference: (number | null)[];
  reading: (number | null)[];
  demand: (number | null)[];
  expected: (number | null)[];
  correction: (number | null)[];
}

export const emptyTrace = (): TraceView => ({ t: [], v: [] });
export const emptyLoopView = (): LoopView => ({ t: [], reference: [], reading: [], demand: [], expected: [], correction: [] });

export interface ReadOptions {
  /** Rows from this time (seconds since the epoch) on; everything held when omitted. */
  fromS?: number;
  /** One row in `every`; the newest is always kept. */
  every?: number;
  /** At most this many rows: `every` rises to fit. */
  maxPoints?: number;
}

export interface TelemetryStoreOptions {
  /** Seconds of history kept per channel and loop. */
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

const LOOP_COLS = 5; // reference, reading, demand, expected, correction

/** A loop's identity across sessions and restarts: what it drives and what it reads. */
const pairOf = (loop: LoopOut) => `${loop.name}|${loop.channel.source}.${loop.channel.measurand}`;

const nan = (x: number | null | undefined) => (x == null ? Number.NaN : x);

/** Points a history request asks for: twice the plot width, at most 4 000. */
export const historyPoints = (): number => Math.min(4000, 2 * Math.max(300, typeof window === "undefined" ? 1440 : window.innerWidth));

/**
 * Every live value the rig publishes, held once and fanned out: samples as
 * a ring buffer per channel, the latest state and a ring of ticks per loop,
 * actuator states, a capped ring of events. Subscribers are told once per
 * animation frame at most, each at its own cadence, and read what they need
 * from the rings (no arrays are built per message). Sockets open on the
 * first subscriber to a stream and close a few seconds after the last one
 * leaves, so a Strict Mode double mount opens each once.
 */
export class TelemetryStore {
  readonly windowS: number;
  private readonly capacity: number;
  private readonly graceMs: number;

  private channels = new Map<string, Ring>();
  private channelVersions = new Map<string, number>();
  private loopRings = new Map<string, Ring>();
  private loopLatest: Record<string, LoopOut> = {};
  private loopVersions = new Map<string, number>();
  private loopsVersion = 0;
  private actuatorStates: Record<string, DeviceState> = {};
  private actuatorVersions = new Map<string, number>();
  private actuatorsVersion = 0;
  private readerRunList: Record<string, ReaderRun> = {};
  private readersVersion = 0;
  private periodsKey = "";
  private eventList: Event[] = [];
  private eventsVersion = 0;
  private readonly eventCap = 2000;
  private eventsSeeded: Promise<void> | null = null;
  private eventsSeedLimit = 0;

  private subs = { samples: new Set<Sub>(), loops: new Set<Sub>(), actuators: new Set<Sub>(), events: new Set<Sub>(), readers: new Set<Sub>(), status: new Set<Sub>() };
  /** Subscribers by key, so a sample touches only those that asked for its channel; and those that asked for any. */
  private byKey = new Map<string, Set<Sub>>();
  private anyKey = { samples: new Set<Sub>(), loops: new Set<Sub>(), actuators: new Set<Sub>(), events: new Set<Sub>(), readers: new Set<Sub>(), status: new Set<Sub>() };
  private dirty = new Set<Sub>();
  private frame: number | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  private sockets = new Map<StoreStream, { subscription: Subscription; count: number; closer: ReturnType<typeof setTimeout> | null }>();
  private statuses: Record<StoreStream, StreamStatus | "idle"> = { samples: "idle", loops: "idle", actuators: "idle", events: "idle", readers: "idle" };
  private statusVersion = 0;
  private lastSeq = new Map<string, number>();
  private gapUntil = 0;
  private gapTimer: ReturnType<typeof setTimeout> | null = null;

  private seededChannels = new Set<string>();
  private pendingSeed: ChannelOut[] = [];
  private seeding: Promise<void> | null = null;
  private newestS = Number.NEGATIVE_INFINITY;
  private seededLoops = new Set<string>();

  constructor(
    private readonly rig: RigClient,
    { windowS = 3600, capacity = 65536, graceMs = 5000 }: TelemetryStoreOptions = {},
  ) {
    this.windowS = windowS;
    this.capacity = capacity;
    this.graceMs = graceMs;
  }

  // region Channels

  private ring(key: string): Ring {
    let ring = this.channels.get(key);
    if (!ring) {
      ring = new Ring({ width: 1, initial: 1024, cap: this.capacity, windowS: this.windowS });
      this.channels.set(key, ring);
    }
    return ring;
  }

  /** Every channel a sample has arrived for. */
  channelKeys(): string[] {
    return [...this.channels.keys()];
  }

  /** The newest point of a channel. */
  latest(key: string): { t: number; v: number } | undefined {
    const ring = this.channels.get(key);
    if (!ring || !ring.length) return undefined;
    return { t: ring.lastT()!, v: ring.last()! };
  }

  /** Bumps whenever the channel gains a point (or its history lands). */
  version(key: string): number {
    return this.channelVersions.get(key) ?? 0;
  }

  /** Copy a channel's rows into `out` (arrays reused), thinned as asked. */
  read(key: string, out: TraceView, options: ReadOptions = {}): TraceView {
    const ring = this.channels.get(key);
    if (!ring) {
      out.t.length = 0;
      out.v.length = 0;
      return out;
    }
    const view: RingView = { t: out.t, cols: [out.v] };
    ring.read(view, options);
    return out;
  }

  /** The rig's clock as far as the samples say: the newest sample time across every channel, or null before any. */
  nowS(): number | null {
    return Number.isFinite(this.newestS) ? this.newestS : null;
  }

  /** Points held for a channel. */
  count(key: string): number {
    return this.channels.get(key)?.length ?? 0;
  }

  private bumpChannel(key: string): void {
    this.channelVersions.set(key, (this.channelVersions.get(key) ?? 0) + 1);
    this.mark("samples", key);
  }

  private onSample(sample: SampleOut): void {
    const time = sample.time_ns / 1e9;
    if (time > this.newestS) this.newestS = time;
    const row = [0];
    for (const measurand in sample.values) {
      const key = `${sample.source}.${measurand}`;
      row[0] = sample.values[measurand]!;
      this.ring(key).push(time, row);
      this.bumpChannel(key);
    }
    const last = this.lastSeq.get(sample.source);
    this.lastSeq.set(sample.source, sample.seq);
    if (last !== undefined && sample.seq > last + 1) {
      // The server dropped samples for us (a lagging client): show it for a while. Only the
      // edges are announced -- a client that is lagging sees a gap in every message.
      const now = Date.now();
      const wasLagging = now < this.gapUntil;
      this.gapUntil = now + 5000;
      if (!wasLagging) {
        this.statusVersion++;
        this.mark("status", null);
      }
      if (this.gapTimer === null) this.gapTimer = setTimeout(() => this.gapEnded(), 5000);
    }
  }

  private gapEnded(): void {
    this.gapTimer = null;
    const remaining = this.gapUntil - Date.now();
    if (remaining > 0) {
      this.gapTimer = setTimeout(() => this.gapEnded(), remaining); // gaps kept coming: try again when the last one ages out
      return;
    }
    this.statusVersion++;
    this.mark("status", null);
    this.flushSoon();
  }

  /** Called once per animation frame for any of `keys` that gained points (`everyMs` apart at least). */
  subscribeTrace(keys: string[], cb: () => void, everyMs = 100): () => void {
    return this.subscribe("samples", new Set(keys), cb, everyMs);
  }

  /** As `subscribeTrace` for one channel, at a readout's cadence. */
  subscribeLatest(key: string, cb: () => void, everyMs = 250): () => void {
    return this.subscribe("samples", new Set([key]), cb, everyMs);
  }

  /**
   * Fill the window from the recording store: one `/series` per channel per
   * session, newest session first until the window is covered, at most 20
   * sessions. Once per channel per page life; points that arrived live
   * before the history landed keep their place after it.
   */
  seed(channels: ChannelOut[]): Promise<void> {
    const wanted = channels.filter((c) => !this.seededChannels.has(channelKey(c)));
    if (!wanted.length) return this.seeding ?? Promise.resolve();
    for (const c of wanted) this.seededChannels.add(channelKey(c));
    // Forty tiles mounting together ask once: the channels are gathered for a tick and fetched as one batch.
    this.pendingSeed.push(...wanted);
    this.seeding ??= new Promise<void>((resolve) => {
      setTimeout(() => {
        const batch = this.pendingSeed;
        this.pendingSeed = [];
        this.seeding = null;
        this.fetchHistory(batch)
          .catch(() => undefined) // no store: live-only is fine
          .then(resolve);
      }, 0);
    });
    return this.seeding;
  }

  private async fetchHistory(channels: ChannelOut[]): Promise<void> {
    const rig = this.rig;
    const [sessions, clock, current] = await Promise.all([rig.sessions(20), rig.clock(), rig.recording().catch(() => null)]);
    const nowS = clock.now_ns / 1e9;
    const horizonS = nowS - this.windowS;
    const parts = new Map<string, Array<{ t: number[]; v: number[] }>>();
    let floorS = Number.POSITIVE_INFINITY; // sessions must not overlap on the axis (see `seedLoops`)
    const maxPoints = historyPoints();
    for (const session of sessions) {
      // Only the current recording may be open; another open session was left by a
      // daemon that died and would overlay stale readings on the live trace.
      if (session.end_ns === null && session.id !== current?.id) continue;
      const startS = session.start_ns / 1e9;
      const endS = session.end_ns === null ? nowS : session.end_ns / 1e9;
      if (endS > floorS) continue;
      floorS = startS;
      if (endS < horizonS) break;
      const startOffset = Math.max(0, Math.floor((horizonS - startS) * 1e9));
      await Promise.all(
        channels.map(async (c) => {
          const series = await rig.series(session.id, c.source, c.measurand, { start_ns: startOffset, max_points: maxPoints }).catch(() => null);
          if (!series || !series.points.length) return;
          const key = channelKey(c);
          (parts.get(key) ?? parts.set(key, []).get(key)!).push({
            t: series.points.map((p) => startS + p.offset_ns / 1e9),
            v: series.points.map((p) => p.value),
          });
        }),
      );
    }
    for (const [key, chunks] of parts) {
      chunks.sort((a, b) => a.t[0]! - b.t[0]!);
      const t = chunks.flatMap((k) => k.t);
      const v = chunks.flatMap((k) => k.v);
      this.ring(key).prepend(t, [v]);
      this.bumpChannel(key);
    }
    this.flushSoon();
  }

  // endregion

  // region Loops

  private loopRing(name: string): Ring {
    let ring = this.loopRings.get(name);
    if (!ring) {
      ring = new Ring({ width: LOOP_COLS, initial: 256, cap: this.capacity, windowS: this.windowS });
      this.loopRings.set(name, ring);
    }
    return ring;
  }

  /** The latest `LoopOut` per name; the same object until a loop changes. */
  loops(): Record<string, LoopOut> {
    return this.loopLatest;
  }

  loop(name: string): LoopOut | undefined {
    return this.loopLatest[name];
  }

  /** Bumps when the named loop ticks, or any loop when `name` is omitted. */
  loopVersion(name?: string): number {
    return name === undefined ? this.loopsVersion : (this.loopVersions.get(name) ?? 0);
  }

  /** Copy a loop's ticks into `out` (arrays reused), thinned as asked. */
  readLoop(name: string, out: LoopView, options: ReadOptions = {}): LoopView {
    const ring = this.loopRings.get(name);
    const cols = [out.reference, out.reading, out.demand, out.expected, out.correction] as number[][];
    if (!ring) {
      out.t.length = 0;
      for (const c of cols) c.length = 0;
      return out;
    }
    ring.read({ t: out.t, cols }, options);
    for (const c of cols) for (let i = 0; i < c.length; i++) if (Number.isNaN(c[i])) (c as (number | null)[])[i] = null;
    return out;
  }

  private onLoops(loops: LoopOut[]): void {
    if (!loops.length) return;
    const next = { ...this.loopLatest };
    const row = new Array<number>(LOOP_COLS);
    for (const loop of loops) {
      next[loop.name] = loop;
      const time = loop.reading ? loop.reading.time_ns / 1e9 : Date.now() / 1000;
      row[0] = nan(setpointOf(loop));
      row[1] = nan(loop.reading ? loop.reading.value : null);
      row[2] = nan(loop.demand);
      row[3] = nan(loop.expected);
      row[4] = nan(loop.correction);
      this.loopRing(loop.name).push(time, row);
      this.loopVersions.set(loop.name, (this.loopVersions.get(loop.name) ?? 0) + 1);
      this.mark("loops", loop.name);
    }
    this.loopLatest = next;
    this.loopsVersion++;
  }

  /** Called when the named loop ticks (any loop when `name` is null), at most every `everyMs`. */
  subscribeLoop(name: string | null, cb: () => void, everyMs = 250): () => void {
    return this.subscribe("loops", name === null ? null : new Set([name]), cb, everyMs);
  }

  /**
   * `GET /api/loops` once (so the latest state is there before the socket's
   * first message) and the window of ticks from the recording store for each
   * loop not yet seeded, matched by (actuator, channel) across sessions.
   */
  seedLoops(every?: number): Promise<void> {
    return this.rig
      .loops()
      .then(async (loops) => {
        const fresh = loops.filter((l) => !(l.name in this.loopLatest));
        if (fresh.length) this.onLoops(fresh);
        const wanted = loops.filter((l) => !this.seededLoops.has(pairOf(l)));
        if (!wanted.length) return;
        for (const l of wanted) this.seededLoops.add(pairOf(l));
        const traces = await this.fetchTicks(wanted, every);
        for (const [name, view] of traces) {
          this.loopRing(name).prepend(view.t, [view.reference, view.reading, view.demand, view.expected, view.correction].map((c) => c.map(nan)));
          this.loopVersions.set(name, (this.loopVersions.get(name) ?? 0) + 1);
          this.mark("loops", name);
        }
        this.loopsVersion++;
        this.flushSoon();
      })
      .catch(() => undefined); // the socket sends every loop on connect anyway; no store is fine
  }

  private async fetchTicks(loops: LoopOut[], every?: number): Promise<Map<string, LoopView>> {
    const rig = this.rig;
    const out = new Map<string, LoopView>();
    const [sessions, clock, current] = await Promise.all([rig.sessions(20).catch(() => []), rig.clock(), rig.recording().catch(() => null)]);
    if (!sessions.length) return out;
    const nowS = clock.now_ns / 1e9; // the rig's now: a simulated clock runs ahead of the wall
    const wanted = new Map(loops.map((l) => [pairOf(l), l.name]));
    const perLoop = new Map<string, Array<{ startS: number; ticks: Awaited<ReturnType<RigClient["ticks"]>> }>>();
    const horizonS = nowS - this.windowS;
    // Sessions must sit one after another on the time axis. A simulated clock
    // restarts with the daemon, so an older session can carry *later* stamps
    // than the current one; such a session cannot share the axis and is skipped.
    let floorS = Number.POSITIVE_INFINITY;
    for (const session of sessions) {
      if (session.end_ns === null && session.id !== current?.id) continue; // an orphan
      const startS = session.start_ns / 1e9;
      const endS = session.end_ns === null ? nowS : session.end_ns / 1e9;
      if (endS > floorS) continue;
      floorS = startS;
      if (endS < horizonS) break;
      const rows = await rig.sessionLoops(session.id).catch(() => []);
      await Promise.all(
        rows.map(async (row) => {
          const pair = `${row.actuator.name}|${row.channel.source.name}.${row.channel.measurand.name}`;
          const name = wanted.get(pair);
          if (!name) return;
          const startOffset = Math.max(0, (horizonS - startS) * 1e9); // history routes take offsets from the session's start
          const ticks = await rig.ticks(session.id, row.actuator.name, { start_ns: Math.floor(startOffset), ...(every && every > 1 ? { every } : {}) }).catch(() => []);
          if (ticks.length) (perLoop.get(name) ?? perLoop.set(name, []).get(name)!).push({ startS, ticks });
        }),
      );
    }
    for (const [name, parts] of perLoop) {
      parts.sort((a, b) => a.startS - b.startS);
      const view = emptyLoopView();
      for (const { startS, ticks } of parts) {
        for (const k of ticks) {
          view.t.push(startS + k.offset_ns / 1e9);
          view.reference.push(k.setpoint);
          view.reading.push(k.reading);
          view.demand.push(k.demand);
          view.expected.push(k.expected ?? null);
          view.correction.push(k.correction ?? null);
        }
      }
      out.set(name, view);
    }
    return out;
  }

  // endregion

  // region Actuators

  /** Every actuator's latest state; the same object until one changes. */
  actuators(): Record<string, DeviceState> {
    return this.actuatorStates;
  }

  actuator(name: string): DeviceState | undefined {
    return this.actuatorStates[name];
  }

  actuatorVersion(name?: string): number {
    return name === undefined ? this.actuatorsVersion : (this.actuatorVersions.get(name) ?? 0);
  }

  private onActuators(states: Array<{ name: string; state: DeviceState }>): void {
    if (!states.length) return;
    const next = { ...this.actuatorStates };
    for (const { name, state } of states) {
      next[name] = state;
      this.actuatorVersions.set(name, (this.actuatorVersions.get(name) ?? 0) + 1);
      this.mark("actuators", name);
    }
    this.actuatorStates = next;
    this.actuatorsVersion++;
  }

  subscribeActuators(name: string | null, cb: () => void, everyMs = 250): () => void {
    return this.subscribe("actuators", name === null ? null : new Set([name]), cb, everyMs);
  }

  // endregion

  // region Readers

  /** Every reader's run (period, running, last read), the same object until one changes. */
  readerRuns(): Record<string, ReaderRun> {
    return this.readerRunList;
  }

  readersVersionNow(): number {
    return this.readersVersion;
  }

  /** The readers' periods as one string; changes only when a period does (a stale threshold's input). */
  readerPeriodsKey(): string {
    return this.periodsKey;
  }

  private onReaders(readers: Array<{ name: string } & ReaderRun>): void {
    if (!readers.length) return;
    const next = { ...this.readerRunList };
    for (const { name, ...run } of readers) next[name] = run;
    this.readerRunList = next;
    this.readersVersion++;
    this.periodsKey = Object.keys(next)
      .sort()
      .map((name) => `${name}=${next[name]!.period_s ?? ""}`)
      .join(",");
    this.mark("readers", null);
  }

  /** Called when a reader reports, at most every `everyMs` (a second by default: a run is a footer line). */
  subscribeReaders(cb: () => void, everyMs = 1000): () => void {
    return this.subscribe("readers", null, cb, everyMs);
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

  /** Per stream: `idle` until someone subscribes, then the socket's state. */
  status(): Readonly<Record<StoreStream, StreamStatus | "idle">> {
    return this.statuses;
  }

  /** Statuses of the streams that have been opened, for a live/reconnecting/offline chip. */
  openStatuses(): StreamStatus[] {
    return STREAMS.map((s) => this.statuses[s]).filter((s): s is StreamStatus => s !== "idle");
  }

  /** True for a few seconds after a sample's `seq` skipped: the server dropped some for this client. */
  lagging(): boolean {
    return Date.now() < this.gapUntil;
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

  private setStatus(stream: StoreStream, status: StreamStatus | "idle"): void {
    if (this.statuses[stream] === status) return;
    this.statuses = { ...this.statuses, [stream]: status };
    this.statusVersion++;
    this.mark("status", null);
    this.flushSoon();
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
    this.acquire(stream);
    return () => {
      this.subs[stream].delete(sub);
      this.anyKey[stream].delete(sub);
      for (const k of keys ?? []) {
        const set = this.byKey.get(index(k));
        set?.delete(sub);
        if (set && !set.size) this.byKey.delete(index(k));
      }
      this.dirty.delete(sub);
      this.release(stream);
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

  private acquire(stream: StoreStream): void {
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
        case "samples":
          this.onSample(message as SampleOut);
          break;
        case "loops":
          this.onLoops((message as { loops: LoopOut[] }).loops);
          break;
        case "actuators":
          this.onActuators((message as { actuators: Array<{ name: string; state: DeviceState }> }).actuators);
          break;
        case "events":
          this.onEvents((message as { events: Event[] }).events);
          break;
        case "readers":
          this.onReaders((message as { readers: Array<{ name: string } & ReaderRun> }).readers);
          break;
      }
      this.flushSoon();
    };
    const subscription = this.rig.stream(stream, {
      onMessage,
      onOpen: () => this.setStatus(stream, "open"),
      onClose: () => this.setStatus(stream, "closed"),
    });
    this.sockets.set(stream, { subscription, count: 1, closer: null });
  }

  private release(stream: StoreStream): void {
    const held = this.sockets.get(stream);
    if (!held) return;
    held.count--;
    if (held.count > 0 || held.closer !== null) return;
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
      held.subscription.close();
      this.setStatus(stream, "idle");
    }
    this.sockets.clear();
    if (this.frame !== null && typeof cancelAnimationFrame === "function") cancelAnimationFrame(this.frame);
    if (this.timer !== null) clearTimeout(this.timer);
    this.frame = null;
    this.timer = null;
  }

  // endregion
}
