// @vitest-environment jsdom
/**
 * The forms and device page after the engine's input and fault waves: a device's inputs line
 * (a constant, a source's quality and age), a values device's sources, the add-device form's
 * number inputs and retry age, and the controller form's `on_fault` and `setpoint_period_s`.
 */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    setData() {}
    setSize() {}
    destroy() {}
  },
}));
import { RigProvider } from "@flyball/react";
import type { ControllerSchema, InputOut, Request, Response, SignalChoice, Transport } from "@flyball/client";
import { InputsLine, describeValueSource, inputValue } from "../src/pages/Devices.js";
import { AddControllerDialog, describeOnFault, draftOnFault } from "../src/pages/Controllers.js";

afterEach(cleanup);

function transport(posted: Array<{ path: string; body: unknown }> = []): Transport {
  return {
    base: "",
    async request({ path, method, body }: Request): Promise<Response> {
      if (method === "POST") {
        posted.push({ path, body });
        return { status: 200, json: { name: "heater.power" } };
      }
      if (path === "/api/controllers" || path === "/api/devices" || path === "/api/history/sessions") return { status: 200, json: [] };
      return { status: 404, json: { detail: "not in this stub" } };
    },
    stream: () => ({ close: () => undefined }),
  };
}

describe("InputsLine", () => {
  const input = (over: Partial<InputOut>): InputOut => ({ name: "dry", label: "", quantity: "", unit: "%", bound: null, quality: "ok", ...over });
  it("shows a constant, a bound source's quality with its reason, and its age", () => {
    render(
      createElement(InputsLine, {
        inputs: {
          dry: input({ name: "dry", constant: 36.5, unit: "%" }),
          wet: input({ name: "wet", bound: "sensors.wet", quality: "stale", reason: "device_offline", age_s: 12.4 }),
          supply: input({ name: "supply", bound: "sensors.supply", quality: "ok", age_s: 1.25 }),
        },
      }),
    );
    expect(screen.getByTestId("input-dry").textContent).toBe("Dry = 36.5 %");
    const wet = screen.getByTestId("input-wet").textContent!;
    expect(wet).toMatch(/^Wet ← sensors\.wet \(stale: device offline\)/);
    expect(wet).toMatch(/· 12 s ago$/);
    expect(screen.getByTestId("input-supply").textContent).toBe("Supply ← sensors.supply · 1.3 s ago");
  });
});

describe("describeValueSource", () => {
  it("says the rig file, a restore, or a write in this run", () => {
    expect(describeValueSource({ origin: "rig_file", initial: 1, actor: null, written_utc_ns: null })).toBe("rig file");
    const at = new Date(1_700_000_000_000).toLocaleString();
    expect(describeValueSource({ origin: "restored", initial: 1, actor: { principal: "ben", kind: "human", via: "http", sid: "", message: "" }, written_utc_ns: 1_700_000_000_000_000_000 })).toBe(`restored, written by ben at ${at}`);
    expect(describeValueSource({ origin: "written", initial: 1, actor: { principal: "ben", kind: "human", via: "http", sid: "", message: "" }, written_utc_ns: 1_700_000_000_000_000_000 })).toBe(`written by ben at ${at}`);
  });
});

describe("inputValue", () => {
  it("a number is a constant binding, anything else an address", () => {
    expect(inputValue(" 36.5 ")).toBe(36.5);
    expect(inputValue("sensors.dry.humidity")).toBe("sensors.dry.humidity");
  });
});

describe("on_fault in the controller form", () => {
  it("omits freeze, sends an action, or {freeze_s, then}", () => {
    expect(draftOnFault("freeze", "30")).toBeUndefined();
    expect(draftOnFault("stop", "")).toBe("stop");
    expect(draftOnFault("stop_device", "30")).toEqual({ freeze_s: 30, then: "stop_device" });
    expect(describeOnFault({ freeze_s: 30, then: "stop_device" })).toBe("freeze 30 s, then stop device");
  });

  it("posts on_fault and setpoint_period_s with the controller", async () => {
    const out: SignalChoice = { address: "heater.power", device: "heater", label: "power", unit: "W", dimension: null, range: null, limits: [0, 100] };
    const measured: SignalChoice = { address: "heater.temperature", device: "heater", label: "temperature", unit: "°C", dimension: null, range: null, limits: null };
    const schema: ControllerSchema = { measured: [measured], outputs: [out], laws: {}, feedforwards: {}, generators: {}, tunings: [], regulated: {}, driven: {} };
    const posted: Array<{ path: string; body: unknown }> = [];
    render(createElement(RigProvider, { transport: transport(posted) }, createElement(AddControllerDialog, { open: true, schema, devices: [], initialTarget: out, onClose: () => undefined, onCreated: () => undefined })));
    fireEvent.click(await screen.findByText("temperature · °C"));
    fireEvent.click(screen.getByText(/^On fault/));
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "on fault" }));
    fireEvent.click(within(screen.getByRole("listbox")).getByText("stop device"));
    fireEvent.change(screen.getByTestId("freeze-s"), { target: { value: "30" } });
    fireEvent.change(screen.getByTestId("setpoint-period"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByTestId("create-controller"));
    await waitFor(() => expect(posted.length).toBe(1));
    expect(posted[0]!.path).toBe("/api/controllers");
    expect(posted[0]!.body).toMatchObject({ output: "heater.power", measured: "heater.temperature", on_fault: { freeze_s: 30, then: "stop_device" }, setpoint_period_s: 0.5 });
  });
});
