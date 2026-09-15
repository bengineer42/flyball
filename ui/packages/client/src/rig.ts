/**
 * The rig over HTTP and websockets, one method per route. Returns the wire
 * types as the server sends them; nothing is reshaped here, so the book's
 * API page is the documentation for this file too.
 */

import type { Request, StreamHandlers, Subscription, Transport } from "./transport.js";
import { RigError } from "./transport.js";
import type {
  ActuatorRow,
  ActuatorSchema,
  ChannelRow,
  ClockOut,
  ChannelOut,
  DeviceSchema,
  DeviceSummary,
  DeviceView,
  ErrorDetail,
  Event,
  EventLevel,
  Health,
  JsonSchema,
  LawConfig,
  LoopOut,
  LoopRow,
  LoopSchema,
  NewLoop,
  RegulateRequest,
  ValueSource,
  ProgramCheck,
  ProgramFormat,
  ProgramRow,
  ProgrammerState,
  ReaderSchema,
  ReaderView,
  ReadingOut,
  RigSchema,
  Series,
  SessionEvent,
  Simulation,
  SimulationPlant,
  SessionRow,
  SourceRow,
  Span,
  StartRecording,
  Tick,
  SignalState,
  SourceOut,
  StreamName,
  Streams,
} from "./wire.js";

export type DeviceKind = "actuators" | "readers";

export interface SeriesQuery {
  start_ns?: number;
  end_ns?: number;
  every?: number;
  bucket_ns?: number;
  max_points?: number;
  [key: string]: number | undefined;
}

export class RigClient {
  constructor(private readonly transport: Transport) {}

  private async call<T>(request: Request): Promise<T> {
    const response = await this.transport.request(request);
    if (response.status >= 400) {
      const detail = (response.json as ErrorDetail | undefined)?.detail ?? `HTTP ${response.status}`;
      throw new RigError(response.status, detail, request.path);
    }
    return response.json as T;
  }

  private get<T>(path: string, query?: Request["query"], signal?: AbortSignal): Promise<T> {
    const request: Request = { method: "GET", path };
    if (query) request.query = query;
    if (signal) request.signal = signal;
    return this.call<T>(request);
  }

  // region Rig

  health(): Promise<Health> {
    return this.get("/api/health");
  }

  /** The rig's clock; every timestamp the rig emits is on this timebase, not the browser's. */
  clock(): Promise<ClockOut> {
    return this.get("/api/clock");
  }

  schema(signal?: AbortSignal): Promise<RigSchema> {
    return this.get("/api/schema", undefined, signal);
  }

  sources(): Promise<SourceOut[]> {
    return this.get("/api/sources");
  }

  source(name: string): Promise<SourceOut> {
    return this.get(`/api/sources/${encodeURIComponent(name)}`);
  }

  latest(source: string, measurand: string): Promise<{ channel: ChannelOut } & ReadingOut> {
    return this.get(`/api/sources/${encodeURIComponent(source)}/${encodeURIComponent(measurand)}`);
  }

  loops(): Promise<LoopOut[]> {
    return this.get("/api/loops");
  }

  loop(name: string): Promise<LoopOut> {
    return this.get(`/api/loops/${encodeURIComponent(name)}`);
  }

  /** What a form needs to make a loop: channels (with dimension), actuators and which channels each may drive, laws, tunings. */
  loopSchema(): Promise<LoopSchema> {
    return this.get("/api/loops/schema");
  }

  makeLoop(body: NewLoop): Promise<LoopOut> {
    return this.call({ method: "POST", path: "/api/loops", body });
  }

  removeLoop(name: string): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/loops/${encodeURIComponent(name)}` });
  }

  /** Aim and hand control to the law. */
  regulate(name: string, body: RegulateRequest): Promise<LoopOut> {
    return this.call({ method: "POST", path: `/api/loops/${encodeURIComponent(name)}/regulate`, body });
  }

  /** Stop regulating; the actuator keeps its last demand. */
  manual(name: string): Promise<LoopOut> {
    return this.call({ method: "POST", path: `/api/loops/${encodeURIComponent(name)}/manual` });
  }

  setReference(name: string, at: number | ValueSource): Promise<LoopOut> {
    return this.call({ method: "PUT", path: `/api/loops/${encodeURIComponent(name)}/reference`, body: { at } });
  }

  tunings(): Promise<Record<string, LawConfig>> {
    return this.get("/api/tunings");
  }

  setTuning(tag: string, config: LawConfig): Promise<LawConfig> {
    return this.call({ method: "PUT", path: `/api/tunings/${encodeURIComponent(tag)}`, body: config });
  }

  // endregion

  // region Devices -- the same shape under /api/actuators and /api/readers

  devices(kind: DeviceKind): Promise<Record<string, DeviceSummary>> {
    return this.get(`/api/${kind}`);
  }

  device(kind: "actuators", name: string): Promise<DeviceView>;
  device(kind: "readers", name: string): Promise<ReaderView>;
  device(kind: DeviceKind, name: string): Promise<DeviceView> {
    return this.get(`/api/${kind}/${encodeURIComponent(name)}`);
  }

  deviceSchema(kind: "actuators", name: string): Promise<ActuatorSchema>;
  deviceSchema(kind: "readers", name: string): Promise<ReaderSchema>;
  deviceSchema(kind: DeviceKind, name: string): Promise<ActuatorSchema | ReaderSchema> {
    return this.get(`/api/${kind}/${encodeURIComponent(name)}/schema`);
  }

  /** Run a marked command; resolves to whatever the method returned. */
  runCommand(kind: DeviceKind, name: string, command: string, args: Record<string, unknown> = {}): Promise<unknown> {
    return this.call({
      method: "POST",
      path: `/api/${kind}/${encodeURIComponent(name)}/${encodeURIComponent(command)}`,
      body: args,
    });
  }

  // endregion

  // region Signals

  signals(): Promise<Record<string, SignalState>> {
    return this.get("/api/signals");
  }

  fireSignal(name: string): Promise<{ name: string; fired: boolean }> {
    return this.call({ method: "POST", path: `/api/signals/${encodeURIComponent(name)}/fire` });
  }

  interruptSignal(name: string): Promise<{ name: string; interrupted: boolean }> {
    return this.call({ method: "POST", path: `/api/signals/${encodeURIComponent(name)}/interrupt` });
  }

  // endregion

  // region History -- reads the store, never the rig

  sessions(limit?: number): Promise<SessionRow[]> {
    return this.get("/api/history/sessions", limit === undefined ? undefined : { limit });
  }

  session(id: number): Promise<SessionRow> {
    return this.get(`/api/history/sessions/${id}`);
  }

  deleteSession(id: number): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/history/sessions/${id}` });
  }

  /** Close an open session: the live one through the rig, an orphaned one in the store. 409 if already ended. */
  endSession(id: number): Promise<SessionRow> {
    return this.call({ method: "POST", path: `/api/history/sessions/${id}/end` });
  }

  series(id: number, source: string, measurand: string, query: SeriesQuery = {}): Promise<Series> {
    return this.get(
      `/api/history/sessions/${id}/series/${encodeURIComponent(source)}/${encodeURIComponent(measurand)}`,
      query,
    );
  }

  sessionSources(id: number): Promise<SourceRow[]> {
    return this.get(`/api/history/sessions/${id}/sources`);
  }

  sessionChannels(id: number): Promise<ChannelRow[]> {
    return this.get(`/api/history/sessions/${id}/channels`);
  }

  sessionActuators(id: number): Promise<ActuatorRow[]> {
    return this.get(`/api/history/sessions/${id}/actuators`);
  }

  sessionLoops(id: number): Promise<LoopRow[]> {
    return this.get(`/api/history/sessions/${id}/loops`);
  }

  ticks(id: number, loop: string, query: { start_ns?: number; end_ns?: number; every?: number } = {}): Promise<Tick[]> {
    return this.get(`/api/history/sessions/${id}/ticks/${encodeURIComponent(loop)}`, query);
  }

  sessionEvents(id: number, query: { start_ns?: number; end_ns?: number } = {}): Promise<SessionEvent[]> {
    return this.get(`/api/history/sessions/${id}/events`, query);
  }

  spans(id: number): Promise<Span[]> {
    return this.get(`/api/history/sessions/${id}/spans`);
  }

  // endregion

  // region Recording -- the one place the rig and the store meet

  /** The open session, or null. */
  recording(): Promise<SessionRow | null> {
    return this.get("/api/recording");
  }

  startRecording(body: StartRecording = {}): Promise<SessionRow> {
    return this.call({ method: "POST", path: "/api/recording", body });
  }

  endRecording(): Promise<SessionRow> {
    return this.call({ method: "POST", path: "/api/recording/end" });
  }

  // endregion

  // region Programs: the library, and the programmer

  programs(): Promise<ProgramRow[]> {
    return this.get("/api/programs/library");
  }

  /** Rescan the daemon's programs directory; resolves to what was newly imported. */
  importPrograms(): Promise<ProgramRow[]> {
    return this.call({ method: "POST", path: "/api/programs/library/import" });
  }

  program(name: string): Promise<ProgramRow> {
    return this.get(`/api/programs/library/${encodeURIComponent(name)}`);
  }

  programHistory(name: string): Promise<ProgramRow[]> {
    return this.get(`/api/programs/library/${encodeURIComponent(name)}/history`);
  }

  checkStoredProgram(name: string): Promise<ProgramCheck> {
    return this.get(`/api/programs/library/${encodeURIComponent(name)}/check`);
  }

  /** Save a version, verbatim in `format`. */
  saveProgram(name: string, format: ProgramFormat, body: string, label?: string, notes?: unknown): Promise<ProgramRow> {
    const payload: Record<string, unknown> = { format, body };
    if (label !== undefined) payload.label = label;
    if (notes !== undefined) payload.notes = notes;
    return this.call({ method: "PUT", path: `/api/programs/library/${encodeURIComponent(name)}`, body: payload });
  }

  deleteProgram(name: string): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/programs/library/${encodeURIComponent(name)}` });
  }

  /** URL of the document as a file, converted to `format` if given; open it or set it as an anchor's href. */
  programDownloadUrl(name: string, format?: ProgramFormat, version?: number): string {
    const q = new URLSearchParams();
    if (format) q.set("format", format);
    if (version !== undefined) q.set("version", String(version));
    const query = q.toString();
    return `/api/programs/library/${encodeURIComponent(name)}/download${query ? `?${query}` : ""}`;
  }

  runStoredProgram(name: string, options: { interrupt?: boolean; version?: number } = {}): Promise<ProgrammerState> {
    const q = new URLSearchParams();
    if (options.interrupt) q.set("interrupt", "true");
    if (options.version !== undefined) q.set("version", String(options.version));
    const query = q.toString();
    return this.call({ method: "POST", path: `/api/programs/library/${encodeURIComponent(name)}/run${query ? `?${query}` : ""}` });
  }

  /** Validate a document (the dialect tree, already parsed) without running it; 422 names the failing step. */
  checkProgram(document: unknown): Promise<unknown> {
    return this.call({ method: "POST", path: "/api/programs/check", body: document });
  }

  programSchema(): Promise<JsonSchema> {
    return this.get("/api/programs/schema");
  }

  programmer(): Promise<ProgrammerState> {
    return this.get("/api/programs/running");
  }

  interruptProgram(): Promise<ProgrammerState> {
    return this.call({ method: "POST", path: "/api/programs/interrupt" });
  }

  // endregion

  /** Subscribe to one of the websocket streams; messages are typed by stream name. */
  stream<S extends StreamName>(name: S, handlers: StreamHandlers<Streams[S]>): Subscription {
    return this.transport.stream(`/ws/${name}`, handlers as StreamHandlers);
  }

  // region Events

  /** The last few hundred events, oldest first; `level` keeps that level and above. */
  events(query: { limit?: number; level?: EventLevel } = {}): Promise<Event[]> {
    return this.get("/api/events", query);
  }

  // endregion

  // region Simulation -- /api/sim: the clock's speed and the plants, and the application's own device

  /** `{simulated: false}` for a rig with real hardware; otherwise the clock, the plants and what has changed. */
  simulation(): Promise<Simulation> {
    return this.get("/api/sim");
  }

  /** Run the rig's time at `speed` times wall time from now on. 409 unless the clock is a scaled one. */
  setSimulationSpeed(speed: number): Promise<{ speed: number }> {
    return this.call({ method: "PUT", path: "/api/sim/clock", body: { speed } });
  }

  /** Advance a stepped clock; 409 if the clock runs on its own. */
  stepSimulation(seconds: number): Promise<{ now_ns: number }> {
    return this.call({ method: "POST", path: "/api/sim/clock/step", body: { seconds } });
  }

  simulationPlant(name: string): Promise<SimulationPlant> {
    return this.get(`/api/sim/plants/${encodeURIComponent(name)}`);
  }

  /** Change some of a plant's parameters while it runs: `{tau_s: 30, noise: 0.2}`; resolves to the whole config. */
  setSimulationPlant(name: string, parameters: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.call({ method: "PUT", path: `/api/sim/plants/${encodeURIComponent(name)}`, body: parameters });
  }

  /** Put a plant at an output and/or input, at once. */
  resetSimulationPlant(name: string, body: { output?: number; input?: number } = {}): Promise<{ input: number; output: number }> {
    return this.call({ method: "POST", path: `/api/sim/plants/${encodeURIComponent(name)}/reset`, body });
  }

  /** Write the current config back to the rig file (or `path`). */
  saveSimulation(path?: string): Promise<{ path: string }> {
    return this.call({ method: "POST", path: "/api/sim/save", body: path === undefined ? {} : { path } });
  }

  /** The application's own simulation device (its physics, its sensors' noise); 404 when it has none. */
  simulationDevice(): Promise<DeviceView> {
    return this.get("/api/sim/device");
  }

  simulationDeviceSchema(): Promise<DeviceSchema> {
    return this.get("/api/sim/device/schema");
  }

  /** Run one of the simulation device's commands; resolves to whatever the method returned. */
  runSimulationCommand(command: string, args: Record<string, unknown> = {}): Promise<unknown> {
    return this.call({ method: "POST", path: `/api/sim/device/${encodeURIComponent(command)}`, body: args });
  }

  // endregion

  /**
   * `GET /api/programs/commands`: the commands' argument schemas as one
   * discriminated union (`$defs` per command, `discriminator.mapping` tag →
   * ref), for rendering a program's steps with titles, units and enums.
   */
  programCommandsSchema(): Promise<JsonSchema> {
    return this.get("/api/programs/commands");
  }
}
