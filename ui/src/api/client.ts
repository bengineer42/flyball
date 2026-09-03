/**
 * The live daemon.
 *
 * Reads come over the telemetry socket rather than by polling: the loop already
 * publishes state on every pass, and a socket that reconnects is cheaper than a
 * timer that hammers a rig busy doing I2C.
 */

import {
  NotImplementedError,
  NO_CAPABILITIES,
  RigError,
  type Capabilities,
  type Handlers,
  type RigTransport,
  type StartControllerRequest,
  type Subscription,
} from "./transport";
import type { FlowRequest, Percent, Program, RigState } from "./types";

const RECONNECT_MS = 2000;

function wsUrl(base: string, path: string): string {
  const url = new URL(base || window.location.origin, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = path;
  return url.toString();
}

export class LiveRig implements RigTransport {
  readonly kind = "live" as const;
  readonly label: string;
  private readonly base: string;

  constructor(base: string) {
    // "" means same-origin, which is what the dev server proxy gives us.
    this.base = base.replace(/\/$/, "");
    this.label = this.base || window.location.origin;
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.base}${path}`, {
        method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (cause) {
      throw new RigError(`Cannot reach the daemon at ${this.label}`, 0);
    }
    if (!response.ok) {
      // FastAPI puts the domain message in `detail`; anything else is a bug or
      // a proxy, and the status is all we can honestly report.
      let detail = `${method} ${path} failed (${response.status})`;
      try {
        const payload = await response.json();
        if (payload && typeof payload.detail === "string") detail = payload.detail;
      } catch {
        /* not JSON */
      }
      throw new RigError(detail, response.status);
    }
    if (response.status === 204) return undefined as T;
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  /** Does this path answer at all? Used only to build the capability report. */
  private async probe(path: string): Promise<boolean> {
    try {
      const response = await fetch(`${this.base}${path}`);
      // 503 means "route exists, nothing attached" — the route is still there.
      return response.status !== 404 && response.status !== 405;
    } catch {
      return false;
    }
  }

  async capabilities(): Promise<Capabilities> {
    let health: { status: string; rig_attached: boolean };
    try {
      health = await this.request("GET", "/api/health");
    } catch {
      return { ...NO_CAPABILITIES };
    }
    const [pumps, controller, programs, history] = await Promise.all([
      this.probe("/api/pumps/"),
      this.probe("/api/controller"),
      this.probe("/api/programs"),
      this.probe("/api/history"),
    ]);
    return {
      reachable: true,
      rigAttached: Boolean(health.rig_attached),
      pumps,
      controller,
      // The recorder has no GET; POST /api/recording/start exists whenever a
      // rig does, so absence of the probe route is not absence of recording.
      recording: true,
      programs,
      history,
      // PUT /api/controller/suspend lives on a router app.py does not mount.
      suspend: false,
    };
  }

  subscribe(handlers: Handlers): Subscription {
    let closed = false;
    let sockets: WebSocket[] = [];
    let timer: number | undefined;

    const open = () => {
      if (closed) return;
      handlers.onStatus("connecting");

      const telemetry = new WebSocket(wsUrl(this.base, "/ws/telemetry"));
      const warnings = new WebSocket(wsUrl(this.base, "/ws/warnings"));
      sockets = [telemetry, warnings];

      telemetry.onopen = () => handlers.onStatus("live");
      telemetry.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        // The socket says so explicitly when the daemon is up but bare.
        if (payload && typeof payload.error === "string" && !("readings" in payload)) {
          handlers.onWarning({ time: Date.now() / 1000, error: payload.error });
          return;
        }
        handlers.onState(payload as RigState);
      };
      warnings.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload && typeof payload.error === "string") {
          handlers.onWarning({
            time: typeof payload.time === "number" ? payload.time : Date.now() / 1000,
            error: payload.error,
          });
        }
      };

      const retry = () => {
        if (closed) return;
        handlers.onStatus("offline");
        sockets.forEach((socket) => socket.close());
        window.clearTimeout(timer);
        timer = window.setTimeout(open, RECONNECT_MS);
      };
      telemetry.onclose = retry;
      telemetry.onerror = retry;
    };

    open();
    return {
      close() {
        closed = true;
        window.clearTimeout(timer);
        sockets.forEach((socket) => socket.close());
      },
    };
  }

  startController(request: StartControllerRequest): Promise<void> {
    return this.request("POST", "/api/controller", request);
  }

  setSetPoint(humidity: Percent): Promise<void> {
    return this.request("PUT", "/api/controller/set_point", { humidity });
  }

  resumeController(): Promise<void> {
    return this.request("POST", "/api/controller/resume");
  }

  async suspendController(): Promise<void> {
    // The route is written but its router is not mounted — see R3.
    throw new NotImplementedError("R3", "Suspending the controller");
  }

  setBlend(flow: FlowRequest, wetFraction: number): Promise<void> {
    return this.request("PUT", "/api/pumps/blend", {
      flow,
      wet_fraction: wetFraction,
    });
  }

  setFlows(wet: number, dry: number): Promise<void> {
    return this.request("PUT", "/api/pumps/flows", { wet, dry });
  }

  setEfforts(wet: number, dry: number): Promise<void> {
    return this.request("PUT", "/api/pumps/efforts", { wet, dry });
  }

  stopPumps(): Promise<void> {
    return this.request("POST", "/api/pumps/stop");
  }

  startRecording(flag?: string): Promise<void> {
    return this.request("POST", "/api/recording/start", { flag: flag ?? null });
  }

  stopRecording(flag?: string): Promise<void> {
    return this.request("POST", "/api/recording/stop", { flag: flag ?? null });
  }

  addFlag(flag: string): Promise<void> {
    return this.request("POST", "/api/recording/flag", { flag });
  }

  stopRig(): Promise<void> {
    return this.request("POST", "/api/stop");
  }

  async runProgram(_program: Program): Promise<void> {
    throw new NotImplementedError("R1", "Running a program on the rig");
  }

  async interruptProgram(): Promise<void> {
    throw new NotImplementedError("R1", "Interrupting a program");
  }
}
