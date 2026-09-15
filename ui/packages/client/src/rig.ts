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
  ChannelOut,
  DeviceSummary,
  DeviceView,
  ErrorDetail,
  Event,
  EventLevel,
  Health,
  LawConfig,
  LoopOut,
  LoopRow,
  ReaderSchema,
  ReaderView,
  ReadingOut,
  RigSchema,
  Series,
  SessionEvent,
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

  ticks(id: number, loop: string, query: { start_ns?: number; end_ns?: number } = {}): Promise<Tick[]> {
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
}
