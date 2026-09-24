/**
 * The rig over HTTP and websockets, one method per route. Returns the wire
 * types as the server sends them; nothing is reshaped here, so the book's
 * API page is the documentation for this file too. Everything is named by
 * address (`furnace.zone1`), a device by name, a controller by its target's
 * address.
 */

import type { Request, StreamHandlers, Subscription, Transport } from "./transport.js";
import { RigError } from "./transport.js";
import type { DashboardDocument, DashboardRow, DashboardWithProblems } from "./dashboards.js";
import { decodeCreationOptions, decodeRequestOptions, encodeCredential } from "./webauthn.js";
import type {
  AuthInfo,
  CommandRunOut,
  PasskeyOut,
  PasskeyListOut,
  Address,
  ClockOut,
  ControllerOut,
  ControllerRow,
  ControllerSchema,
  DeviceOut,
  DeviceRow,
  DeviceSchema,
  DeviceView,
  ErrorDetail,
  Event,
  Health,
  JsonSchema,
  LawConfig,
  LinkEntry,
  NewController,
  NewDevice,
  ProgramCheck,
  ProgramFormat,
  ProgramRow,
  ProgrammerState,
  ReadOut,
  SetpointSpec,
  RegulateRequest,
  Severity,
  RigDocument,
  RigSchema,
  RigVersion,
  RigVersionDetail,
  SaveResult,
  StartSpec,
  Series,
  SessionEvent,
  RunnerInfo,
  KeepRange,
  SessionRow,
  SignalRow,
  Simulation,
  SimulationPlant,
  Span,
  StartRecording,
  StopReport,
  StreamName,
  Streams,
  Tick,
  ActivityOut,
  WriteOut,
  WriteRow,
  WriteStateRow,
} from "./wire.js";

/** What a download comes as. A whole session can also come as a `zip` of every table plus its metadata. */
export type ExportFormat = "csv" | "json";
export type SessionExportFormat = ExportFormat | "zip";

export interface SessionExportQuery {
  format?: SessionExportFormat;
  /** `wide`: a column per signal, each row holding every signal's last value. `long`: a row per raw value. */
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

export interface WindowQuery {
  start_ns?: number;
  end_ns?: number;
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

const enc = encodeURIComponent;

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

  /** A route's URL under the transport's base, for a link the browser follows itself (a download).
   * No credential travels here: a signed-in browser carries its session cookie regardless, and a
   * bearer token never appears in a URL (a plain navigation cannot set a header for one anyway). */
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

  /** `GET /api/schema`: every device's schema, by name. */
  schema(signal?: AbortSignal): Promise<RigSchema> {
    return this.get("/api/schema", undefined, signal);
  }

  tunings(): Promise<Record<string, LawConfig>> {
    return this.get("/api/tunings");
  }

  tuning(name: string): Promise<LawConfig> {
    return this.get(`/api/tunings/${enc(name)}`);
  }

  /** Store `config` under `name` on the live rig, replacing any tuning already there. */
  setTuning(name: string, config: LawConfig): Promise<LawConfig> {
    return this.call({ method: "PUT", path: `/api/tunings/${enc(name)}`, body: config });
  }

  // endregion

  // region Devices -- one list, whatever the driver

  /** Every device: its tree with the latest values and write states, commands, state, conditions, run. */
  devices(signal?: AbortSignal): Promise<DeviceOut[]> {
    return this.get("/api/devices", undefined, signal);
  }

  /** One device by name; 404 if none has it. */
  device(name: string, signal?: AbortSignal): Promise<DeviceOut> {
    return this.get(`/api/devices/${enc(name)}`, undefined, signal);
  }

  /** Config, settings, state, every signal and every command's request as JSON Schema. */
  deviceSchema(name: string, signal?: AbortSignal): Promise<DeviceSchema> {
    return this.get(`/api/devices/${enc(name)}/schema`, undefined, signal);
  }

  /** Run a marked command: what the method returned (`result`) and the controllers it put in manual (`interrupted`). A command that succeeds on an offline device restarts its polling. */
  command(name: string, command: string, args: Record<string, unknown> = {}): Promise<CommandRunOut> {
    return this.call({ method: "POST", path: `/api/devices/${enc(name)}/commands/${enc(command)}`, body: args });
  }

  /** Poll an offline device again on its period. */
  restartDevice(name: string): Promise<DeviceOut> {
    return this.call({ method: "POST", path: `/api/devices/${enc(name)}/restart` });
  }

  /**
   * Write values to writable signals under a device as one write, committed
   * at once; keys are names relative to the device (dotted under a
   * namespace: `position.x`), values in each signal's unit. Resolves to the
   * write state of each signal set, by address. 409 for a signal a
   * controller drives, a `together` group set in part, or a signal that is
   * not writable; 404 for a name not under the device.
   */
  writeNode(name: string, values: Record<string, number>): Promise<Record<Address, WriteOut>> {
    return this.call({ method: "PUT", path: `/api/devices/${enc(name)}/write`, body: values });
  }

  /** The single-signal write: `value` in the signal's unit. 409 if the address is a namespace or a controller drives it. */
  write(address: Address, value: number): Promise<Record<Address, WriteOut>> {
    return this.call({ method: "PUT", path: `/api/signals/${enc(address)}`, body: value });
  }

  /** Build a device on the rig's links and put it on the rig: bound, polling, recorded. 409 for a name in use, 404 for an unknown link or an unresolved bound address, 422 for an unknown driver or a config it refuses. */
  addDevice(body: NewDevice): Promise<DeviceOut> {
    return this.call({ method: "POST", path: "/api/devices", body });
  }

  /** Take a device off the rig with everything that hung off it; 404 if there is none. */
  removeDevice(name: string): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/devices/${enc(name)}` });
  }

  // endregion

  // region Composition -- links and whole documents; the rig's versions; saving it

  /** Build a link and hold it under `body.name`; 409 if the name is taken, 422 for a bad config. */
  addLink(body: LinkEntry): Promise<LinkEntry> {
    return this.call({ method: "POST", path: "/api/links", body });
  }

  /** Drop a link no device is built on; 409 while one is. */
  removeLink(name: string): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/links/${enc(name)}` });
  }

  /** Add a document's links, devices and controllers to the running rig, in that order; validated whole before anything is built, so a failure part-way leaves what was built before it. */
  addDocument(document: Partial<RigDocument> & Record<string, unknown>): Promise<RigDocument> {
    return this.call({ method: "POST", path: "/api/rig", body: document });
  }

  /** The running rig as a rig file would build it: links, devices, controllers as they are now. */
  rigDocument(signal?: AbortSignal): Promise<RigDocument> {
    return this.get("/api/rig/document", undefined, signal);
  }

  /** What differs between the running rig and the files it was loaded from, as an overlay (a key removed appears as `null`); `{}` when nothing has changed. */
  rigChanges(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return this.get("/api/rig/changes", undefined, signal);
  }

  /** Every version of the rig this store has seen, newest first: when, and why it changed. */
  // region Auth

  /**
   * Who this caller is here and what this rig's door is like (`GET /api/auth`). Answers anonymous for
   * no credential or a stale session cookie, but 401 for an `Authorization` header that is not a
   * valid flyball token (a proxy's `Basic`, say), with or without a session cookie.
   */
  auth(): Promise<AuthInfo> {
    return this.get("/api/auth");
  }

  /**
   * Trade the admin password (a `password`-shape front) or a pasted token (a `bare` runner) for a
   * session cookie the browser then carries on every request, socket and download. 401 wrong;
   * 429 with `Retry-After` when limited.
   */
  login(credential: { password: string } | { token: string }): Promise<AuthInfo> {
    return this.call({ method: "POST", path: "/api/auth/login", body: credential });
  }

  /** Clear the session cookie; the answer is the anonymous view. */
  logout(): Promise<AuthInfo> {
    return this.call({ method: "POST", path: "/api/auth/logout" });
  }

  // Passkeys: for Phase 3 (passkeys in the Go front). Dormant in Phase 1 -- nothing serves
  // /api/auth/passkey/* yet; the UI calls these only when AuthInfo's `login.passkey` is true.

  /** Register a passkey for this browser's authenticator, under `label`. Needs an already-
   * authenticated (or anonymous-operate) session -- there is no separate bootstrap. Drives
   * `navigator.credentials.create()` itself: the server's challenge, base64url-decoded to the
   * `ArrayBuffer`s the browser API wants, and the resulting credential re-encoded to JSON. */
  async registerPasskey(label: string): Promise<PasskeyOut> {
    const options = await this.call<Record<string, any>>({ method: "POST", path: "/api/auth/passkey/challenge" });
    const credential = (await navigator.credentials.create({
      publicKey: decodeCreationOptions(options),
    })) as PublicKeyCredential;
    return this.call({
      method: "POST",
      path: "/api/auth/passkey/register",
      body: { credential: encodeCredential(credential), label },
    });
  }

  /** Sign in with a passkey already registered on this runner: `navigator.credentials.get()`,
   * then the same trade for a session cookie `login` makes. No prior session needed. */
  async loginWithPasskey(): Promise<AuthInfo> {
    const options = await this.call<Record<string, any>>({ method: "POST", path: "/api/auth/passkey/login/challenge" });
    const credential = (await navigator.credentials.get({
      publicKey: decodeRequestOptions(options),
    })) as PublicKeyCredential;
    return this.call({
      method: "POST",
      path: "/api/auth/passkey/login",
      body: { credential: encodeCredential(credential) },
    });
  }

  /** This runner's registered passkeys, and whether they survive a restart. */
  listPasskeys(): Promise<PasskeyListOut> {
    return this.get("/api/auth/passkey");
  }

  /** Forget a passkey; anyone using it is refused from their next request. */
  deletePasskey(id: number): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/auth/passkey/${id}` });
  }

  /**
   * Stop the rig for everyone: interrupt any running program, put every controller in manual and
   * hold every writable device (`POST <root>/api/rig/stop`). Needs `OPERATE`; never rate-limited.
   * Answers 501 until package A8 wires the real stopper -- callers must surface that honestly
   * rather than treating the call as having stopped anything.
   */
  stopRig(reason?: string): Promise<StopReport> {
    return this.call({ method: "POST", path: "/api/rig/stop", body: reason === undefined ? {} : { reason } });
  }

  // endregion

  /** How this runner serves: its resolved `runner:` config (`GET /api/runner`). */
  runner(): Promise<RunnerInfo> {
    return this.get("/api/runner");
  }

  /** Ask the runner to stop (409 unless it allows it). */
  shutdownRunner(): Promise<{ detail: string }> {
    return this.call({ method: "POST", path: "/api/runner/shutdown" });
  }

  /** Ask the runner to restart in place: the same command, the rig rebuilt; sockets drop for a few seconds. */
  restartRunner(): Promise<{ detail: string }> {
    return this.call({ method: "POST", path: "/api/runner/restart" });
  }

  rigVersions(limit?: number): Promise<RigVersion[]> {
    return this.get("/api/rig/versions", limit === undefined ? undefined : { limit });
  }

  /** One version with the document it held. */
  rigVersion(id: number): Promise<RigVersionDetail> {
    return this.get(`/api/rig/versions/${id}`);
  }

  /** Make the running rig that version again: links, devices and controllers rebuilt to match it; records a version of its own. */
  restoreVersion(id: number): Promise<RigDocument> {
    return this.call({ method: "POST", path: `/api/rig/versions/${id}/restore` });
  }

  /** Write the running rig out. No `path`: the changes since the files were loaded, to the overlay beside them (409 if the rig was not started from a file). A `path`: the whole rig, flattened (422 for a suffix the runner does not write, 409 for one of the loaded files unless `overwrite`). */
  saveRig(body: { path?: string; overwrite?: boolean } = {}): Promise<SaveResult> {
    return this.call({ method: "POST", path: "/api/rig/save", body });
  }

  /** The rig file's JSON schema, with every driver and link type installed here. */
  rigSchema(signal?: AbortSignal): Promise<JsonSchema> {
    return this.get("/api/rig/schema", undefined, signal);
  }

  // endregion

  // region Reading by address

  /**
   * What the address names: `{reading}` for a signal, `{sample}` for an
   * atomic namespace, `{samples}` for a device or a namespace read over
   * several transactions; `fresh` reads the hardware first, which is how a
   * setting (`rw`, never published) is read. 503 until the first read, 404
   * for an unknown address. Several addresses at once come back as a list
   * in the order given; a fresh read then costs each device one read.
   */
  read(address: Address, fresh?: boolean): Promise<ReadOut>;
  read(addresses: Address[], fresh?: boolean): Promise<ReadOut[]>;
  read(target: Address | Address[], fresh = false): Promise<ReadOut | ReadOut[]> {
    const query = fresh ? { fresh: true } : undefined;
    if (Array.isArray(target)) return this.get<ReadOut[]>("/api/read", { at: target.join(","), ...query });
    return this.get<ReadOut>(`/api/read/${enc(target)}`, query);
  }

  // endregion

  // region Controllers -- named by the target's address

  controllers(): Promise<ControllerOut[]> {
    return this.get("/api/controllers");
  }

  /** `address` is the controller's name: its target's. */
  controller(address: Address): Promise<ControllerOut> {
    return this.get(`/api/controllers/${enc(address)}`);
  }

  /** The rig's default controller; 503 when there is none. */
  defaultController(): Promise<ControllerOut> {
    return this.get("/api/controllers/default");
  }

  /** What a form needs to make a controller: every publishing and every writable signal, laws, feedforwards, tunings, what is spoken for. */
  controllerSchema(): Promise<ControllerSchema> {
    return this.get("/api/controllers/schema");
  }

  /** Attach a controller; 409 if a signal is already spoken for or the units disagree, 404 for an unknown address. */
  createController(body: NewController): Promise<ControllerOut> {
    return this.call({ method: "POST", path: "/api/controllers", body });
  }

  /** Detach a controller. It is put in manual first so the target holds its last demand. */
  detachController(address: Address): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/controllers/${enc(address)}` });
  }

  /**
   * Aim at `at` (a value, `process`/`setpoint`/`demand`, or a generator spec such as
   * `{type: "linear_ramp_setpoint", pace: {per_minute: 10}, end: 75}`, which ramps from
   * the current setpoint or reading) and hand control to the law; the handover's demand
   * is committed at once.
   */
  regulate(address: Address, body: RegulateRequest): Promise<ControllerOut> {
    return this.call({ method: "POST", path: `/api/controllers/${enc(address)}/regulate`, body });
  }

  /** Stop regulating; the target keeps its last demand and takes demands directly. */
  manual(address: Address): Promise<ControllerOut> {
    return this.call({ method: "POST", path: `/api/controllers/${enc(address)}/manual` });
  }

  /**
   * Move the setpoint, or start following a generator spec, without touching the mode or the
   * law's state. `start` says where a generator begins (a value, `setpoint`, `measured`);
   * omitted, the current setpoint while regulating, else the last reading.
   */
  setSetpoint(address: Address, at: SetpointSpec, start?: StartSpec | null): Promise<ControllerOut> {
    return this.call({ method: "PUT", path: `/api/controllers/${enc(address)}/setpoint`, body: start == null ? { at } : { at, start } });
  }

  // endregion

  // region Activities -- what the rig is waiting on, and answering it

  /** Every registered activity by name: pending, or settled but not yet taken down. */
  activities(): Promise<Record<string, ActivityOut>> {
    return this.get("/api/activities");
  }

  activity(name: string): Promise<ActivityOut> {
    return this.get(`/api/activities/${enc(name)}`);
  }

  /** Settle the activity as met; `fired` false if it had already settled. */
  fireActivity(name: string): Promise<{ name: string; fired: boolean }> {
    return this.call({ method: "POST", path: `/api/activities/${enc(name)}/fire` });
  }

  /** Cancel the activity; the program stops at this step. */
  cancelActivity(name: string): Promise<{ name: string; cancelled: boolean }> {
    return this.call({ method: "POST", path: `/api/activities/${enc(name)}/cancel` });
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

  /** Keep a range of the scratch record as a closed session of its own; the new session. */
  keepRange(id: number, body: KeepRange): Promise<SessionRow> {
    return this.call({ method: "POST", path: `/api/history/sessions/${id}/keep`, body });
  }

  /** Pin (or unpin) a session: pinned ones are never aged out. */
  pinSession(id: number, pinned: boolean): Promise<SessionRow> {
    return this.call({ method: "PUT", path: `/api/history/sessions/${id}`, body: { pinned } });
  }

  /** Set a session's display name (`details.name`, what `sessionName` shows). */
  renameSession(id: number, name: string): Promise<SessionRow> {
    return this.call({ method: "PATCH", path: `/api/history/sessions/${id}`, body: { name } });
  }

  /**
   * Delete several sessions; there is no bulk route, so each is its own
   * request, a few at a time (`concurrency`). Resolves to the ones that
   * failed, each with its error message -- the ones not listed succeeded.
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

  sessionDevices(id: number): Promise<DeviceRow[]> {
    return this.get(`/api/history/sessions/${id}/devices`);
  }

  /** Every signal the session recorded; `address` keys the series and writes. */
  sessionSignals(id: number): Promise<SignalRow[]> {
    return this.get(`/api/history/sessions/${id}/signals`);
  }

  /** The writable signals whose write states the session recorded. */
  sessionWrites(id: number): Promise<WriteRow[]> {
    return this.get(`/api/history/sessions/${id}/writes`);
  }

  sessionControllers(id: number): Promise<ControllerRow[]> {
    return this.get(`/api/history/sessions/${id}/controllers`);
  }

  /** One signal over a window, optionally downsampled (one of `every`, `bucket_ns`, `max_points`). */
  series(id: number, address: Address, query: SeriesQuery = {}): Promise<Series> {
    return this.get(`/api/history/sessions/${id}/series/${enc(address)}`, query);
  }

  /** What one writable signal was set to, in order. */
  writeStates(id: number, address: Address, query: WindowQuery = {}): Promise<WriteStateRow[]> {
    return this.get(`/api/history/sessions/${id}/writes/${enc(address)}`, query);
  }

  /** A controller's steps; `controller` is the address of the signal it drives. */
  ticks(id: number, controller: Address, query: WindowQuery & { every?: number } = {}): Promise<Tick[]> {
    return this.get(`/api/history/sessions/${id}/ticks/${enc(controller)}`, query);
  }

  sessionEvents(id: number, query: WindowQuery = {}): Promise<SessionEvent[]> {
    return this.get(`/api/history/sessions/${id}/events`, query);
  }

  /** In start order; nest by `parent_id`. */
  spans(id: number): Promise<Span[]> {
    return this.get(`/api/history/sessions/${id}/spans`);
  }

  // The export routes answer with a file (`Content-Disposition: attachment`),
  // so the client builds their URLs and the page points an anchor at them;
  // nothing is fetched here.

  /** URL of the whole session as a file: every signal, or `zip` for the tables, the controllers' ticks, the writes, the events and the metadata. */
  exportUrl(id: number, { format = "csv", layout = "wide", step_s }: SessionExportQuery = {}): string {
    return this.url(`/api/history/sessions/${id}/export`, { format, layout: format === "zip" ? undefined : layout, step_s });
  }

  /** URL of one signal's series as a file. */
  seriesExportUrl(id: number, address: Address, format: ExportFormat = "csv"): string {
    return this.url(`/api/history/sessions/${id}/series/${enc(address)}/export`, { format });
  }

  /** URL of one signal's write states as a file. */
  writesExportUrl(id: number, address: Address, format: ExportFormat = "csv"): string {
    return this.url(`/api/history/sessions/${id}/writes/${enc(address)}/export`, { format });
  }

  /** URL of one controller's ticks as a file. */
  ticksExportUrl(id: number, controller: Address, format: ExportFormat = "csv"): string {
    return this.url(`/api/history/sessions/${id}/ticks/${enc(controller)}/export`, { format });
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

  /** Rescan the runner's programs directory; resolves to what was newly imported. */
  importPrograms(): Promise<ProgramRow[]> {
    return this.call({ method: "POST", path: "/api/programs/library/import" });
  }

  program(name: string): Promise<ProgramRow> {
    return this.get(`/api/programs/library/${enc(name)}`);
  }

  programHistory(name: string): Promise<ProgramRow[]> {
    return this.get(`/api/programs/library/${enc(name)}/history`);
  }

  checkStoredProgram(name: string): Promise<ProgramCheck> {
    return this.get(`/api/programs/library/${enc(name)}/check`);
  }

  /** Save a version, verbatim in `format`. */
  saveProgram(name: string, format: ProgramFormat, body: string, label?: string, notes?: unknown): Promise<ProgramRow> {
    const payload: Record<string, unknown> = { format, body };
    if (label !== undefined) payload.label = label;
    if (notes !== undefined) payload.notes = notes;
    return this.call({ method: "PUT", path: `/api/programs/library/${enc(name)}`, body: payload });
  }

  deleteProgram(name: string): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/programs/library/${enc(name)}` });
  }

  /** Move a program, with its whole version history, under a new name. 409 if the name is taken. */
  renameProgram(name: string, newName: string): Promise<ProgramRow[]> {
    return this.call({ method: "POST", path: `/api/programs/library/${enc(name)}/rename`, body: { name: newName } });
  }

  /** URL of the document as a file, converted to `format` if given; open it or set it as an anchor's href. */
  programDownloadUrl(name: string, format?: ProgramFormat, version?: number): string {
    const q = new URLSearchParams();
    if (format) q.set("format", format);
    if (version !== undefined) q.set("version", String(version));
    const query = q.toString();
    return `/api/programs/library/${enc(name)}/download${query ? `?${query}` : ""}`;
  }

  runStoredProgram(name: string, options: { cancel?: boolean; version?: number } = {}): Promise<ProgrammerState> {
    const q = new URLSearchParams();
    if (options.cancel) q.set("cancel", "true");
    if (options.version !== undefined) q.set("version", String(options.version));
    const query = q.toString();
    return this.call({ method: "POST", path: `/api/programs/library/${enc(name)}/run${query ? `?${query}` : ""}` });
  }

  /**
   * `POST /api/programs/run`: start a program document that is not stored -- one step run on its
   * own, or a few -- without saving it to the library. `cancel` cancels whatever is running
   * instead of the 409 a busy programmer gives.
   */
  runProgram(document: unknown, options: { cancel?: boolean } = {}): Promise<ProgrammerState> {
    return this.call({ method: "POST", path: `/api/programs/run${options.cancel ? "?cancel=true" : ""}`, body: document });
  }

  /** Validate a document (the dialect tree, already parsed) without running it: `ProgramCheck` with a warning per step naming something the rig lacks; 422 names a step that fails to parse. */
  checkProgram(document: unknown): Promise<ProgramCheck> {
    return this.call({ method: "POST", path: "/api/programs/check", body: document });
  }

  programSchema(): Promise<JsonSchema> {
    return this.get("/api/programs/schema");
  }

  /**
   * `GET /api/programs/commands`: the commands' argument schemas as one
   * discriminated union (`$defs` per command, `discriminator.mapping` command →
   * ref), for rendering a program's steps with titles, units and enums.
   */
  programCommandsSchema(): Promise<JsonSchema> {
    return this.get("/api/programs/commands");
  }

  programmer(): Promise<ProgrammerState> {
    return this.get("/api/programs/running");
  }

  cancelProgram(): Promise<ProgrammerState> {
    return this.call({ method: "POST", path: "/api/programs/cancel" });
  }

  // endregion

  // region Events

  /** The last few hundred events, oldest first; `severity` keeps that severity and above. */
  events(query: { limit?: number; severity?: Severity } = {}): Promise<Event[]> {
    return this.get("/api/events", query);
  }

  /** Subscribe to one of the websocket streams; messages are typed by stream name. */
  stream<S extends StreamName>(name: S, handlers: StreamHandlers<Streams[S]>): Subscription {
    return this.transport.stream(`/ws/${name}`, handlers as StreamHandlers);
  }

  // endregion

  // region Simulation -- /api/sim: the clock's speed and the plants, and the application's own device

  /** `{simulated: false}` for a rig with real hardware; otherwise the clock, the plants and what has changed. */
  simulation(): Promise<Simulation> {
    return this.get("/api/sim");
  }

  /** Run the rig's time at `speed` times wall time from now on. 409 on a stepped clock. */
  setSimulationSpeed(speed: number): Promise<{ speed: number }> {
    return this.call({ method: "PUT", path: "/api/sim/clock", body: { speed } });
  }

  /** Advance a stepped clock; 409 if the clock runs on its own. */
  advanceSimulation(seconds: number): Promise<{ now_ns: number }> {
    return this.call({ method: "POST", path: "/api/sim/clock/advance", body: { seconds } });
  }

  simulationPlant(name: string): Promise<SimulationPlant> {
    return this.get(`/api/sim/plants/${enc(name)}`);
  }

  /** Change some of a plant's parameters while it runs: `{tau_s: 30, noise: 0.2}`; resolves to the whole config. */
  setSimulationPlant(name: string, parameters: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.call({ method: "PUT", path: `/api/sim/plants/${enc(name)}`, body: parameters });
  }

  /** Put a plant at an output and/or input, at once. */
  resetSimulationPlant(name: string, body: { output?: number; input?: number } = {}): Promise<{ input: number; output: number }> {
    return this.call({ method: "POST", path: `/api/sim/plants/${enc(name)}/reset`, body });
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
    return this.call({ method: "POST", path: `/api/sim/device/${enc(command)}`, body: args });
  }

  // endregion

  // region Dashboards -- the UI's own documents, saved per rig with a version history

  /** The newest version of each of this rig's dashboards (`every`: every rig's). */
  dashboards(every = false): Promise<DashboardRow[]> {
    return this.get("/api/dashboards", every ? { every: true } : undefined);
  }

  /** The newest version, plus what it names that this rig lacks. */
  dashboard(name: string): Promise<DashboardWithProblems> {
    return this.get(`/api/dashboards/${enc(name)}`);
  }

  dashboardHistory(name: string): Promise<DashboardRow[]> {
    return this.get(`/api/dashboards/${enc(name)}/history`);
  }

  /** Save a version under `name`; the body is the whole document. */
  saveDashboard(name: string, body: DashboardDocument): Promise<DashboardWithProblems> {
    return this.call({ method: "PUT", path: `/api/dashboards/${enc(name)}`, body });
  }

  /** Move a dashboard, with its history, under a new name. 409 if the name is taken. */
  renameDashboard(name: string, newName: string): Promise<DashboardRow[]> {
    return this.call({ method: "POST", path: `/api/dashboards/${enc(name)}/rename`, body: { name: newName } });
  }

  deleteDashboard(name: string): Promise<void> {
    return this.call({ method: "DELETE", path: `/api/dashboards/${enc(name)}` });
  }

  /** JSON Schema of the document, for checking an import before it is shown. */
  dashboardSchema(): Promise<JsonSchema> {
    return this.get("/api/dashboards/schema");
  }

  // endregion
}
