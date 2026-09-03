/**
 * What the UI needs from a rig, whether that rig is the daemon or the mock.
 *
 * Every command returns a promise that rejects with a `RigError` carrying the
 * daemon's `detail` string, so a 409 ("no pumps attached") reaches the operator
 * as prose rather than a status code.
 */

import type {
  FlowRequest,
  LawConfig,
  Percent,
  Program,
  RigState,
  Warning,
} from "./types";

/** Which parts of the API this rig actually answers. */
export interface Capabilities {
  /** The daemon is reachable at all. */
  reachable: boolean;
  /** A rig is attached to the daemon (health.rig_attached). */
  rigAttached: boolean;
  pumps: boolean;
  controller: boolean;
  recording: boolean;
  /** PLANNED — see API-REQUIREMENTS.md R1. */
  programs: boolean;
  /** PLANNED — logged history for the charts on reload. See R4. */
  history: boolean;
  /** PLANNED — suspending the controller from the UI. See R3. */
  suspend: boolean;
}

export const NO_CAPABILITIES: Capabilities = {
  reachable: false,
  rigAttached: false,
  pumps: false,
  controller: false,
  recording: false,
  programs: false,
  history: false,
  suspend: false,
};

export class RigError extends Error {
  readonly status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.name = "RigError";
    this.status = status;
  }
}

/** Raised for a route the daemon does not serve yet. Callers show the doc note. */
export class NotImplementedError extends RigError {
  readonly requirement: string;
  constructor(requirement: string, what: string) {
    super(`${what} is not implemented by the daemon yet (${requirement})`, 501);
    this.name = "NotImplementedError";
    this.requirement = requirement;
  }
}

export interface Subscription {
  close(): void;
}

export interface Handlers {
  onState(state: RigState): void;
  onWarning(warning: Warning): void;
  /** Connection state, so the header can say "live", "connecting" or "offline". */
  onStatus(status: LinkStatus): void;
}

export type LinkStatus = "connecting" | "live" | "offline";

export interface StartControllerRequest {
  humidity: Percent;
  flow?: FlowRequest | null;
  control_law?: LawConfig | null;
}

export interface RigTransport {
  readonly kind: "live" | "mock";
  /** A one-line description of where this data is coming from. */
  readonly label: string;

  capabilities(): Promise<Capabilities>;
  subscribe(handlers: Handlers): Subscription;

  startController(request: StartControllerRequest): Promise<void>;
  setSetPoint(humidity: Percent): Promise<void>;
  resumeController(): Promise<void>;
  suspendController(): Promise<void>;

  setBlend(flow: FlowRequest, wetFraction: number): Promise<void>;
  setFlows(wet: number, dry: number): Promise<void>;
  setEfforts(wet: number, dry: number): Promise<void>;
  stopPumps(): Promise<void>;

  startRecording(flag?: string): Promise<void>;
  stopRecording(flag?: string): Promise<void>;
  addFlag(flag: string): Promise<void>;

  stopRig(): Promise<void>;

  runProgram(program: Program): Promise<void>;
  interruptProgram(): Promise<void>;
}
