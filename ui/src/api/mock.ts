/**
 * A simulated rig, in the browser.
 *
 * Same purpose as `build_simulated_rig` in `humctrl.daemon`: somewhere to drive
 * the whole interface without hardware. It is also the only way to exercise the
 * absent-sensor paths on demand — a real rig either has a dry-line sensor or it
 * does not, whereas here it is a switch.
 *
 * The plant is a first-order lag towards the humidity the current blend implies,
 * which is the same relationship `DESIGN.md` derives, with the settling time
 * lumped into one time constant. It predicts nothing about a real chamber; it
 * exists so the screens have something honest-looking to render.
 */

import {
  NO_CAPABILITIES,
  RigError,
  type Capabilities,
  type Handlers,
  type RigTransport,
  type StartControllerRequest,
  type Subscription,
} from "./transport";
import type {
  FlowRequest,
  LawConfig,
  Percent,
  Program,
  ProgramStatus,
  PumpsSpec,
  RigState,
  Step,
} from "./types";
import { expectedHumidityFromFraction, resolveFlow, calculateWetFraction } from "../domain/flows";

export interface MockOptions {
  /** Is a sensor fitted to the dry line? Off by default: most rigs have none. */
  drySensor: boolean;
  wetSensor: boolean;
  /** In-line flow meters (R5). Off by default: no rig has these yet. */
  flowSensors: boolean;
  /** Source humidities used when the lines are not sensed. */
  dryHumidity: Percent;
  wetHumidity: Percent;
  /** Chamber time constant, seconds. */
  tau: number;
  /** Measurement noise, percentage points peak. */
  noise: number;
  /** Fail the process sensor, to exercise the fault path. */
  faultProcess: boolean;
}

export const DEFAULT_MOCK_OPTIONS: MockOptions = {
  drySensor: false,
  wetSensor: false,
  flowSensors: false,
  dryHumidity: 8,
  wetHumidity: 92,
  tau: 12,
  noise: 0.15,
  faultProcess: false,
};

const SPEC: PumpsSpec = {
  max_flows: { wet: 1.8, dry: 2.0 },
  full_range_max_flow: 1.8,
  units: "L/min",
};

const TICK_MS = 500;

interface MockController {
  law: LawConfig;
  setPoint: Percent;
  flow: FlowRequest;
  suspended: boolean;
  integral: number;
}

export class MockRig implements RigTransport {
  readonly kind = "mock" as const;
  readonly label = "Simulated rig (in browser)";

  private options: MockOptions;
  private handlers: Handlers | null = null;
  private timer: number | undefined;
  private readonly started = Date.now() / 1000;

  private efforts = { wet: 0, dry: 0 };
  private humidity = 45;
  private temperature = 21;
  private recording = false;
  private controller: MockController | null = null;
  private program: ProgramStatus | null = null;
  private programTimer: number | undefined;
  private interrupted = false;

  constructor(options: Partial<MockOptions> = {}) {
    this.options = { ...DEFAULT_MOCK_OPTIONS, ...options };
  }

  configure(options: Partial<MockOptions>): void {
    this.options = { ...this.options, ...options };
  }

  async capabilities(): Promise<Capabilities> {
    return {
      ...NO_CAPABILITIES,
      reachable: true,
      rigAttached: true,
      pumps: true,
      controller: true,
      recording: true,
      // The mock runs programs locally so the builder can be exercised; the
      // daemon cannot yet, and says so.
      programs: true,
      history: false,
      suspend: true,
    };
  }

  // region Plant

  private get sourceHumidities(): { dry: Percent; wet: Percent } {
    return { dry: this.options.dryHumidity, wet: this.options.wetHumidity };
  }

  private get flows() {
    return {
      wet: this.efforts.wet * SPEC.max_flows.wet,
      dry: this.efforts.dry * SPEC.max_flows.dry,
    };
  }

  private get wetFraction(): number {
    const { wet, dry } = this.flows;
    const total = wet + dry;
    return total > 0 ? wet / total : 0;
  }

  private get expectedHumidity(): Percent | null {
    const { wet, dry } = this.flows;
    if (wet + dry <= 0) return null;
    const source = this.sourceHumidities;
    return expectedHumidityFromFraction(source.dry, source.wet, this.wetFraction);
  }

  private jitter(value: number): number {
    return value + (Math.random() - 0.5) * 2 * this.options.noise;
  }

  private tick(): void {
    const dt = TICK_MS / 1000;
    const target = this.expectedHumidity;
    if (target !== null) {
      this.humidity += (dt / this.options.tau) * (target - this.humidity);
    }
    this.temperature += (dt / 60) * (21.4 - this.temperature);

    if (this.controller && !this.controller.suspended) this.step(dt);
    this.publish();
  }

  private step(dt: number): void {
    const controller = this.controller;
    if (!controller) return;
    const source = this.sourceHumidities;
    const reading = this.humidity;

    // Open loop puts the blend straight at the set point's ratio; closed loop
    // corrects that demand with the law, the split-range structure the Python
    // controller uses.
    let demand = controller.setPoint;
    if (controller.law.type !== "open_loop") {
      const error = controller.setPoint - reading;
      const kp = controller.law.kp ?? 0;
      const ki = controller.law.ki ?? 0;
      controller.integral += error * dt;
      // Crude anti-windup: the demand is clamped, so the integral is too.
      controller.integral = Math.max(-200, Math.min(200, controller.integral));
      demand = controller.setPoint + kp * error + ki * controller.integral;
    }

    const fraction = calculateWetFraction(source.dry, source.wet, demand);
    const wet =
      fraction.kind === "fraction"
        ? fraction.value
        : fraction.kind === "railed"
          ? fraction.rail === "wet"
            ? 1
            : 0
          : this.wetFraction;

    const total = resolveFlow(controller.flow, wet, SPEC);
    this.applyFlows({ wet: total * wet, dry: total * (1 - wet) });
  }

  private applyFlows(flows: { wet: number; dry: number }): void {
    this.efforts = {
      wet: Math.max(0, Math.min(1, flows.wet / SPEC.max_flows.wet)),
      dry: Math.max(0, Math.min(1, flows.dry / SPEC.max_flows.dry)),
    };
  }

  private state(): RigState {
    const now = Date.now() / 1000;
    const source = this.sourceHumidities;
    const flows = this.flows;
    return {
      time: now - this.started,
      running: true,
      recording: this.recording,
      pumps: { flows, efforts: { ...this.efforts } },
      pumps_spec: SPEC,
      controller: this.controller
        ? {
            type: this.controller.law.type,
            set_point: this.controller.setPoint,
            demand: this.controller.setPoint,
            flow: { value: this.controller.flow.value, scale: this.controller.flow.scale },
            suspended: this.controller.suspended,
            law: this.controller.law,
          }
        : null,
      readings: {
        process: this.options.faultProcess
          ? null
          : { time: now, humidity: this.jitter(this.humidity), temperature: this.jitter(this.temperature), source: "chamber" },
        dry: this.options.drySensor
          ? { time: now, humidity: this.jitter(source.dry), temperature: this.jitter(this.temperature - 0.4), source: "dry" }
          : null,
        wet: this.options.wetSensor
          ? { time: now, humidity: this.jitter(source.wet), temperature: this.jitter(this.temperature + 0.3), source: "wet" }
          : null,
        errors: this.options.faultProcess
          ? { process: "Error reading from sensor 'chamber': simulated I2C failure" }
          : {},
      },
      expected_humidity: this.expectedHumidity,
      measured_flows: this.options.flowSensors
        ? { wet: this.jitter(flows.wet * 0.98), dry: this.jitter(flows.dry * 0.97) }
        : null,
      program: this.program,
    };
  }

  private publish(): void {
    this.handlers?.onState(this.state());
  }

  private warn(message: string): void {
    this.handlers?.onWarning({ time: Date.now() / 1000, error: message });
  }

  // endregion

  subscribe(handlers: Handlers): Subscription {
    this.handlers = handlers;
    handlers.onStatus("live");
    this.publish();
    this.timer = window.setInterval(() => this.tick(), TICK_MS);
    return {
      close: () => {
        window.clearInterval(this.timer);
        this.handlers = null;
      },
    };
  }

  private requireController(): MockController {
    if (!this.controller) throw new RigError("No controller is running", 409);
    return this.controller;
  }

  async startController(request: StartControllerRequest): Promise<void> {
    if (this.controller) throw new RigError("A controller is already running", 409);
    this.controller = {
      law: request.control_law ?? { type: "open_loop" },
      setPoint: request.humidity,
      flow: request.flow ?? { value: 1, scale: "full_range_max" },
      suspended: false,
      integral: 0,
    };
    this.publish();
  }

  async setSetPoint(humidity: Percent): Promise<void> {
    this.requireController().setPoint = humidity;
    this.publish();
  }

  async resumeController(): Promise<void> {
    const controller = this.requireController();
    controller.suspended = false;
    controller.integral = 0;
    this.publish();
  }

  async suspendController(): Promise<void> {
    this.requireController().suspended = true;
    this.publish();
  }

  async setBlend(flow: FlowRequest, wetFraction: number): Promise<void> {
    if (this.controller) this.controller.suspended = true;
    const total = resolveFlow(flow, wetFraction, SPEC);
    const flows = { wet: total * wetFraction, dry: total * (1 - wetFraction) };
    if (
      flow.scale === "absolute" &&
      flow.on_overdrive !== "clamp" &&
      (flows.wet > SPEC.max_flows.wet || flows.dry > SPEC.max_flows.dry)
    ) {
      throw new RigError(
        `Flows (wet ${flows.wet.toFixed(2)}, dry ${flows.dry.toFixed(2)}) exceed the pumps`,
        400,
      );
    }
    this.applyFlows(flows);
    this.publish();
  }

  async setFlows(wet: number, dry: number): Promise<void> {
    if (wet > SPEC.max_flows.wet || dry > SPEC.max_flows.dry) {
      throw new RigError(
        `Flows exceed the pumps (max wet ${SPEC.max_flows.wet}, dry ${SPEC.max_flows.dry} ${SPEC.units})`,
        400,
      );
    }
    if (this.controller) this.controller.suspended = true;
    this.applyFlows({ wet, dry });
    this.publish();
  }

  async setEfforts(wet: number, dry: number): Promise<void> {
    if (this.controller) this.controller.suspended = true;
    this.efforts = { wet: Math.max(0, Math.min(1, wet)), dry: Math.max(0, Math.min(1, dry)) };
    this.publish();
  }

  async stopPumps(): Promise<void> {
    if (this.controller) this.controller.suspended = true;
    this.efforts = { wet: 0, dry: 0 };
    this.publish();
  }

  async startRecording(flag?: string): Promise<void> {
    this.recording = true;
    if (flag) this.warn(`flag: ${flag}`);
    this.publish();
  }

  async stopRecording(flag?: string): Promise<void> {
    this.recording = false;
    if (flag) this.warn(`flag: ${flag}`);
    this.publish();
  }

  async addFlag(flag: string): Promise<void> {
    this.warn(`flag: ${flag}`);
  }

  async stopRig(): Promise<void> {
    await this.interruptProgram();
    this.controller = null;
    this.efforts = { wet: 0, dry: 0 };
    this.recording = false;
    this.publish();
  }

  // region Programs

  async runProgram(program: Program): Promise<void> {
    if (this.program?.running) throw new RigError("A program is already running", 409);
    this.interrupted = false;
    this.program = {
      program_id: program.id,
      name: program.name,
      running: true,
      step: 0,
      step_count: program.steps.length,
      started: Date.now() / 1000,
    };
    if (program.record_as) await this.startRecording(program.record_as);
    void this.walk(program);
  }

  async interruptProgram(): Promise<void> {
    this.interrupted = true;
    window.clearTimeout(this.programTimer);
    if (this.program) this.program = { ...this.program, running: false };
    this.publish();
  }

  private async walk(program: Program): Promise<void> {
    for (let index = 0; index < program.steps.length; index += 1) {
      if (this.interrupted) break;
      this.program = { ...this.program!, step: index, running: true };
      this.publish();
      try {
        await this.runStep(program.steps[index]);
      } catch (error) {
        this.warn(`step ${index + 1} (${program.steps[index].kind}): ${(error as Error).message}`);
        break;
      }
    }
    this.program = this.program ? { ...this.program, running: false } : null;
    this.publish();
  }

  private wait(ms: number): Promise<void> {
    return new Promise((resolve) => {
      this.programTimer = window.setTimeout(resolve, ms);
    });
  }

  /** One step, with the blocking semantics of `humctrl.runners`. */
  private async runStep(step: Step): Promise<void> {
    switch (step.kind) {
      case "start_controller":
        return this.startController({
          humidity: step.humidity,
          flow: step.flow,
          control_law: step.control_law,
        });
      case "set_point":
        return this.setSetPoint(step.humidity);
      case "set_blend":
        return this.setBlend(step.flow, step.wet_fraction);
      case "set_flows":
        return this.setFlows(step.wet, step.dry);
      case "set_efforts":
        return this.setEfforts(step.wet, step.dry);
      case "stop_pumps":
        return this.stopPumps();
      case "start_recording":
        return this.startRecording(step.name ?? undefined);
      case "stop_recording":
        return this.stopRecording(step.name ?? undefined);
      case "flag":
        return this.addFlag(step.flag);
      case "ramp":
        return this.runRamp(step);
      case "hold":
        return this.runHold(step);
    }
  }

  private async runRamp(step: Extract<Step, { kind: "ramp" }>): Promise<void> {
    const from =
      step.start_from === "value"
        ? (step.start_value ?? this.humidity)
        : step.start_from === "target"
          ? (this.controller?.setPoint ?? this.humidity)
          : this.humidity;

    if (!this.controller) {
      await this.startController({ humidity: from, flow: step.flow, control_law: step.control_law });
    } else {
      await this.setSetPoint(from);
    }

    const seconds =
      step.pace.kind === "duration"
        ? step.pace.seconds
        : Math.abs(step.target - from) /
          (step.pace.value / (step.pace.per === "second" ? 1 : step.pace.per === "minute" ? 60 : 3600));

    const start = Date.now();
    while (!this.interrupted) {
      const elapsed = (Date.now() - start) / 1000;
      if (elapsed >= seconds) break;
      await this.setSetPoint(from + (step.target - from) * (elapsed / seconds));
      await this.wait(TICK_MS);
    }
    if (!this.interrupted) await this.setSetPoint(step.target);
  }

  private async runHold(step: Extract<Step, { kind: "hold" }>): Promise<void> {
    const target = step.target ?? this.controller?.setPoint ?? this.humidity;
    let mode = step.mode;
    if (mode === "cross") mode = this.humidity > target ? "below" : "above";

    const satisfied = () => {
      switch (mode) {
        case "above":
          return this.humidity > target + step.tolerance;
        case "below":
          return this.humidity < target - step.tolerance;
        default:
          return Math.abs(this.humidity - target) <= step.tolerance;
      }
    };

    const start = Date.now();
    let held = 0;
    let readings = 0;
    while (!this.interrupted) {
      if (step.timeout && (Date.now() - start) / 1000 > step.timeout) {
        this.warn(`hold timed out after ${step.timeout}s`);
        return;
      }
      if (satisfied()) {
        held += TICK_MS / 1000;
        readings += 1;
        if (held >= step.min_duration && readings >= step.min_readings) return;
      } else {
        held = 0;
        readings = 0;
      }
      await this.wait(TICK_MS);
    }
  }

  // endregion
}
