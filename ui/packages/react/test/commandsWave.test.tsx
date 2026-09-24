// @vitest-environment jsdom
/**
 * Commands after the engine's command waves: the form sends only what the person edited; a
 * command that drives the device and does not interrupt says it is refused while a controller
 * regulates the device; a refused write shows the rig's reason as it comes; a controller's
 * `interrupted` event reads "Put in manual".
 */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { CommandSchema, ControllerOut, Request, Response, SignalOut, SignalSchema, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { CommandForm, commandDrives, editedArguments } from "../src/panels/CommandForm.js";
import { WritePanel } from "../src/panels/WritePanel.js";
import { describeEvent } from "../src/panels/EventsPanel.js";

const REGULATING = { name: "blender.fraction", output_signal: "blender.fraction", measured_signal: "chamber.rh", mode: "regulating" } as unknown as ControllerOut;

function transport(controllers: ControllerOut[] = [], writeRefusal?: string): Transport {
  return {
    base: "",
    async request({ path }: Request): Promise<Response> {
      if (path === "/api/controllers") return { status: 200, json: controllers };
      if (path === "/api/devices" || path === "/api/history/sessions") return { status: 200, json: [] };
      if (path === "/api/recording") return { status: 200, json: null };
      if (writeRefusal && path.startsWith("/api/signals/")) return { status: 409, json: { detail: writeRefusal } };
      return { status: 404, json: { detail: "not in this stub" } };
    },
    stream: () => ({ close: () => undefined }),
  };
}

afterEach(cleanup);

const command = (over: Partial<CommandSchema>): CommandSchema => ({
  description: null,
  arguments: { type: "object", properties: {} },
  simulation: false,
  commit: false,
  sets_mode: null,
  interrupts: false,
  writes: [],
  demand_of: null,
  ...over,
});

const SET_FLOWS = command({
  arguments: {
    type: "object",
    properties: {
      dry: { type: "number", "x-signal": "blender.flows.dry" },
      wet: { type: "number", "x-signal": "blender.flows.wet" },
      mode: { type: "string", default: "clamp" },
    },
  },
});

describe("editedArguments", () => {
  it("drops what the form opened with: a prefilled current value, a schema default", () => {
    expect(editedArguments({ dry: 3, wet: 2, mode: "clamp" }, { dry: 1, wet: 2 }, SET_FLOWS.arguments)).toEqual({ dry: 3 });
  });
  it("keeps an argument with nothing to open with, and an edited default", () => {
    const schema = { type: "object", properties: { n: { type: "number" }, mode: { type: "string", default: "clamp" } } };
    expect(editedArguments({ n: 5, mode: "scale" }, {}, schema)).toEqual({ n: 5, mode: "scale" });
  });
  it("reads an object with its keys reordered as unchanged", () => {
    expect(editedArguments({ flow: { b: 1, a: 2 } }, { flow: { a: 2, b: 1 } }, { type: "object", properties: {} })).toEqual({});
  });
});

describe("commandDrives", () => {
  const signals = { "flows.dry": { role: "demand" }, "blend_flow": { role: "setting" } } as unknown as Record<string, SignalSchema>;
  it("a mode, a writes, or a linked demand drives; a linked setting does not", () => {
    expect(commandDrives(command({ sets_mode: "humidity" }))).toBe(true);
    expect(commandDrives(command({ writes: ["on"] }))).toBe(true);
    expect(commandDrives(SET_FLOWS, "blender", signals)).toBe(true);
    const setting = command({ arguments: { type: "object", properties: { flow: { type: "number", "x-signal": "blender.blend_flow" } } } });
    expect(commandDrives(setting, "blender", signals)).toBe(false);
  });
});

describe("CommandForm while a controller regulates the device", () => {
  it("says the command is refused and holds its button", async () => {
    render(createElement(RigProvider, { transport: transport([REGULATING]) }, createElement(CommandForm, { name: "open", command: command({ writes: ["valve"] }), device: "blender", onRun: async () => undefined })));
    expect((await screen.findByTestId("command-refused", undefined, { timeout: 2000 })).textContent).toBe("refused while blender.fraction regulates: put it in manual first");
    expect((screen.getByRole("button", { name: "Open" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("says nothing for a command that interrupts, or on another device", async () => {
    render(
      createElement(RigProvider, { transport: transport([REGULATING]) },
        createElement(CommandForm, { name: "set_flows", command: command({ writes: ["valve"], interrupts: true }), device: "blender", onRun: async () => undefined }),
        createElement(CommandForm, { name: "open", command: command({ writes: ["valve"] }), device: "other", onRun: async () => undefined }),
      ),
    );
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.queryByTestId("command-refused")).toBeNull();
  });
  it("sends only the edited argument", async () => {
    const onRun = vi.fn(async () => undefined);
    render(createElement(RigProvider, { transport: transport() }, createElement(CommandForm, { name: "set_flows", command: SET_FLOWS, device: "blender", onRun })));
    const boxes = screen.getAllByRole("spinbutton") as HTMLInputElement[];
    fireEvent.change(boxes[0]!, { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Set flows" }));
    await waitFor(() => expect(onRun).toHaveBeenCalled());
    expect(onRun).toHaveBeenCalledWith({ dry: 3 });
  });
});

describe("WritePanel refusal", () => {
  it("shows the rig's reason word for word", async () => {
    const reason = "'blender.flows.dry' [RP] is not writable: it is a readback, moved by the command 'set_flows' (it puts a regulating controller in manual)";
    const signal = { name: "dry", address: "blender.flows.dry", access: "rw", label: "", quantity: "", unit: "L/min", dimension: null, dtype: "float", shape: [], role: "demand", tags: {}, initial: null, range: null, precision: 1, limits: null, warning: null, alarm: null, poll_s: null, stale_after_s: null, write: null, latest: null } as unknown as SignalOut;
    render(createElement(RigProvider, { transport: transport([], reason) }, createElement(WritePanel, { signal })));
    fireEvent.change(screen.getByLabelText(/demand$/), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Set" }));
    expect((await screen.findByText(reason)).textContent).toBe(reason);
  });
});

describe("describeEvent", () => {
  it("a controller's interrupted is 'Put in manual'; a program's stays 'Interrupted'", () => {
    expect(describeEvent({ scope: "controller", code: "interrupted" })).toBe("Put in manual");
    expect(describeEvent({ scope: "program", code: "interrupted" })).toBe("Interrupted");
  });
});
