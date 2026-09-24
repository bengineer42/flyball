// @vitest-environment jsdom
/**
 * A dashboard's write controls follow `canWrite` (operate, and the document not `readonly`):
 * the recording widget's Start and End are live with it and disabled, still shown, without it.
 */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
// Importing `@flyball/react` pulls in uPlot, which probes `matchMedia` at module scope (see App.test.tsx).
vi.mock("uplot", () => ({ default: class {} }));
import { RigDataContext, type RigData } from "../src/dashboard/context.js";
import { recording } from "../src/widgets/Recording.js";

afterEach(cleanup);

const widget = { id: "r", type: "recording", x: 0, y: 0, w: 6, h: 3, config: { controls: true } };

function show(canWrite: boolean, open: boolean) {
  const data = {
    canWrite,
    recording: {
      data: open ? { id: 4, name: "run", start_ns: 0, end_ns: null } : null,
      error: null,
      loading: false,
      refresh: () => undefined,
      start: async () => undefined,
      end: async () => undefined,
    },
  } as unknown as RigData;
  render(createElement(RigDataContext.Provider, { value: data }, createElement(recording.Component, { widget, config: widget.config, editing: false })));
}

const button = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;

describe("recording widget and canWrite", () => {
  it("Start is live when the dashboard may write", () => {
    show(true, false);
    expect(button(/start/i).disabled).toBe(false);
  });
  it("Start is shown but disabled on a read-only dashboard", () => {
    show(false, false);
    expect(button(/start/i).disabled).toBe(true);
  });
  it("End is shown but disabled on a read-only dashboard", () => {
    show(false, true);
    expect(button(/end/i).disabled).toBe(true);
  });
  it("End is live when the dashboard may write", () => {
    show(true, true);
    expect(button(/end/i).disabled).toBe(false);
  });
});
