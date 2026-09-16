/**
 * The rig over HTTP and websockets, one method per route. Returns the wire
 * types as the server sends them; nothing is reshaped here, so the book's
 * API page is the documentation for this file too.
 */

import type { Request, StreamHandlers, Subscription, Transport } from "./transport.js";
import { RigError } from "./transport.js";
import type { DashboardDocument, DashboardRow } from "./dashboards.js";
import type {
  ActuatorRow,
  ActuatorSchema,
  ChannelRow,
  ClockOut,
  ChannelOut,
  DeviceOut,
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

/** What a download comes as. A whole session can also come as a `zip` of every table plus its metadata. */
export type ExportFormat = "csv" | "json";
export type SessionExportFormat = ExportFormat | "zip";

export interface SessionExportQuery {
  format?: SessionExportFormat;
  /** `wide`: a column per channel, each row holding every channel's last value. `long`: a row per raw value. */
  layout?: "wide" | "long";
  /** Resample a wide table onto this grid, in seconds. */
  step_s?: number;
}

export interface SeriesQuery {
  start_ns?: number;
  end_ns?: number;
  every?: number;
  bucket_ns?: number;
  max_points?: number;
  [key: string]: number | undefined;
}

/**
 * An error body's `detail` as one line: the string FastAPI sends, or a 422's
 * list of `{loc, msg}` as `field: message; ...`. Undefined when there is none.
 */
function describeDetail(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const lines = detail.map((item) => {
      const { loc, msg } = (item ?? {}) as { loc?: unknown[]; msg?: unknown };
      const where = Array.isArray(loc) ? loc.filter((l) => l !== "body").join(".") : "";
      return where ? `${where}: ${String(msg ?? JSON.stringify(item))}` : String(msg ?? JSON.stringify(item));
    });
    if (lines.length) return lines.join("; ");
  }
  if (detail !== undefined && detail !== null) return JSON.stringify(detail);
  return undefined;
}

export class RigClient {
  constructor(private readonly transport: Transport) {}

  private async call<T>(request: Request): Promise<T> {
    const response = await this.transport.request(request);
    if (response.status >= 400) {
      const body = response.json as ErrorDetail | { detail: unknown } | undefined;
      throw new RigError(response.status, describeDetail(body?.detail) ?? response.text?.trim() ?? `HTTP ${response.status}`, request.path);
    }
    return response.json as T;
  }

  /** A route's URL under the transport's base, for a link the browser follows itself (a download). */
  private url(path: string, query: Record<string, string | number | undefined> = {}): string {
    const q = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) if (value !== undefined) q.set(key, String(value));
    const search = q.toString();
    return `${this.transport.base ?? ""}${path}${search ? `?${search}` : ""}`;
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

  /**
   * Delete several sessions; there is no bulk route, so each is its own
   * request, a few at a time (`concurrency`). Resolves to the ones that
   * failed, each with its error message — the ones not listed succeeded.
   */
  async deleteSessions(ids: number[], concurrency = 4): Promise<Array<{ id: number; error: string }>> {
    const queue = [...ids];
    const failed: Array<{ id: number; error: string }> = [];
    const worker = async () => {
      let id: number | undefined;
      while ((id = queue.shift()) !== undefined) {
        try {
          await this.deleteSession(id);
        } catch (e) {
          failed.push({ id, error: e instanceof Error ? e.message : String(e) });
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(concurrency, ids.length) }, worker));
    return failed;
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

  // The export routes answer with a file (`Content-Disposition: attachment`),
  // so the client builds their URLs and the page points an anchor at them;
  // nothing is fetched here.

  /** URL of the whole session as a file: every channel, or `zip` for the tables, the loops' ticks, the events and the metadata. */
  exportUrl(id: number, { format = "csv", layout = "wide", step_s }: SessionExportQuery = {}): string {
    return this.url(`/api/history/sessions/${id}/export`, { format, layout: format === "zip" ? undefined : layout, step_s });
  }

  /** URL of one channel's series as a file. */
  seriesExportUrl(id: number, source: string, measurand: string, format: ExportFormat = "csv"): string {
    return this.url(
      `/api/history/sessions/${id}/series/${encodeURIComponent(source)}/${encodeURIComponent(measurand)}/export`,
      { format },
    );
  }

  /** URL of one loop's ticks as a file. */
  ticksExportUrl(id: number, loop: string, format: ExportFormat = "csv"): string {
    return this.url(`/api/history/sessions/${id}/ticks/${encodeURIComponent(loop)}/export`, { format });
  }

  /** URL of the session's events as a file. */
  eventsExportUrl(id: number, format: ExportFormat = "csv"): string {
    return this.url(`/api/history/sessions/${id}/events/export`, { format });
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

  /** Move a program, with its whole version history, under a new name. 409 if the name is taken. */
  renameProgram(name: string, newName: string): Promise<ProgramRow[]> {
    return this.call({ method: "POST", path: `/api/programs/library/${encodeURIComponent(name)}/rename`, body: { name: newName } });
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
  /** Normalise and validate a document; 422 (a `RigError`) names the step that failed. */
  checkProgram(document: unknown): Promise<ProgramCheck> {
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

  // region Dashboards -- the UI's own documents, saved per rig with a version history

  /** The newest version of each of this rig's dashboards (`every`: every rig's). */
  dashboards(every = false): Promise<DashboardRow[]> {
    return this.get("/api/dashboards", every ? { every: true } : undefined);
  }

  dashboard(name: string): Promise<DashboardRow> {
    return this.get(`/api/dashboards/${encodeURIComponent(name)}`);
  }

  dashboardHistory(name: string): Promise<DashboardRow[]> {
    return this.get(`/api/dashboards/${encodeURIComponent(name)}/history`);
  }

  /** Save a version under `name`; the body is the whole document. */
  saveDashboard(name: string, body: DashboardDocument): Promise<DashboardRow> {
    return this.call({ method: "PUT", path: `/api/dashboards/${encodeURIComponent(name)}`, body });
  }

  /** Move a dashboard, with its history, under a new name. 409 if the name is taken. */
  renameDashboard(name: string, newName: string): Promise<DashboardRow[]> {
    return this.call({ method: "POST", path: `/api/dashboards/${encodeURIComponent(name)}/rename`, body: { name: newName } });
  }

  deleteDashboard(name: string): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/dashboards/${encodeURIComponent(name)}` });
  }

  /** JSON Schema of the document, for checking an import before it is shown. */
  dashboardSchema(): Promise<JsonSchema> {
    return this.get("/api/dashboards/schema");
  }

  // endregion

  // region All devices -- one namespace: readers, actuators and an application's own device

  /**
   * `GET /api/devices`: every reader, actuator and application device, once,
   * by name -- not `devices(kind)` above, which is the `/api/actuators` or
   * `/api/readers` shape for one kind at a time. Use this to list or resolve
   * a name without knowing its kind first.
   */
  allDevices(): Promise<Record<string, DeviceOut>> {
    return this.get("/api/devices");
  }

  resolveDevice(name: string): Promise<DeviceOut> {
    return this.get(`/api/devices/${encodeURIComponent(name)}`);
  }

  // endregion
}
