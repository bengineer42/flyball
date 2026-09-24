// @vitest-environment jsdom
/** `POST /commands/{c}` answers `{result, interrupted}`: `useCommands` keeps the method's result as the result and the interrupted controllers beside it, and the form says whom it put in manual. */
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import type { CommandSchema, Request, Response, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { useCommands } from "../src/hooks/useDevices.js";
import { CommandForm } from "../src/panels/CommandForm.js";

function transport(): Transport {
  return {
    base: "",
    async request({ path }: Request): Promise<Response> {
      if (path === "/api/devices/blender/commands/set_flows") return { status: 200, json: { result: 42, interrupted: [{ controller: "chamber.rh", was: "regulating" }] } };
      if (path === "/api/devices" || path === "/api/controllers" || path === "/api/history/sessions") return { status: 200, json: [] };
      return { status: 404, json: { detail: "not in this stub" } };
    },
    stream: () => ({ close: () => undefined }),
  };
}

afterEach(cleanup);

describe("a command's response", () => {
  it("is unwrapped: the result is the method's, the interrupted controllers are kept beside it", async () => {
    const t = transport();
    const wrapper = ({ children }: { children: ReactNode }) => createElement(RigProvider, { transport: t }, children);
    const { result } = renderHook(() => useCommands("blender"), { wrapper });
    let returned: unknown;
    await act(async () => {
      returned = await result.current.run("set_flows", {});
    });
    expect(returned).toBe(42);
    expect(result.current.results.set_flows).toMatchObject({ result: 42, interrupted: [{ controller: "chamber.rh", was: "regulating" }] });
  });

  it("names in the form the controllers it put in manual", () => {
    const command: CommandSchema = { name: "set_flows", label: "Set flows", arguments: { type: "object", properties: {} } } as unknown as CommandSchema;
    render(
      createElement(RigProvider, { transport: transport() },
        createElement(CommandForm, { name: "set_flows", command, onRun: async () => undefined, result: { result: 42, interrupted: [{ controller: "chamber.rh", was: "regulating" }], at: 0 } }),
      ),
    );
    expect(screen.getByTestId("interrupted").textContent).toBe("put chamber.rh in manual");
  });
});
