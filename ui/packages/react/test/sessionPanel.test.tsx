// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { Request, Response, SessionRow, SignalRow, StreamHandlers, Subscription, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { SessionPanel } from "../src/panels/SessionPanel.js";
import type { SessionDetail } from "../src/hooks/useSession.js";

// `MultiSeries`/`TimeSeries` import `uplot` at module scope, which probes `matchMedia`
// (unavailable in jsdom) as a side effect of import alone -- stubbed the same way
// `controllerTrendExpand.test.tsx` and `canOperate.test.tsx` do.
vi.mock("uplot", () => ({
  default: class {
    cursor = { idx: null };
    data = [[]];
    setData() {}
    setSize() {}
    setLegend() {}
    setScale() {}
    destroy() {}
  },
}));
class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/**
 * A controllable `IntersectionObserver`: `observe` records the callback
 * against the element (never fires on its own), so a test decides exactly
 * when a chart becomes "on screen" -- that is the trigger `useSession.ts`'s
 * `useSessionSeries` fetch is gated on. `fireVisible(el)` finds the callback
 * registered for that element (or the ancestor `.fb-channel` it belongs to)
 * and reports it intersecting.
 */
let observed: Array<{ el: Element; cb: IntersectionObserverCallback }> = [];
class FakeIntersectionObserver {
  cb: IntersectionObserverCallback;
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
  }
  observe(el: Element) {
    observed.push({ el, cb: this.cb });
  }
  unobserve() {}
  disconnect() {}
}
function fireVisible(el: Element) {
  const entry = observed.find((o) => o.el === el);
  entry?.cb([{ isIntersecting: true } as IntersectionObserverEntry], entry as unknown as IntersectionObserver);
}

function signalRow(address: string, unit: string, extra: Partial<SignalRow> = {}): SignalRow {
  return {
    id: 1,
    device_id: 1,
    address,
    quantity: "temperature",
    unit,
    access: "rp",
    dtype: "float",
    shape: [],
    label: null,
    range: null,
    precision: 2,
    warn: null,
    alarm: null,
    limits: null,
    ...extra,
  };
}

function session(id: number): SessionRow {
  return {
    id,
    start_ns: 0,
    end_ns: 60_000_000_000,
    version: null,
    config: null,
    hardware: null,
    details: null,
  };
}

/** A minimal shell -- `useSession`'s own resolution, no series or ticks. */
function shell(id: number, signals: SignalRow[]): SessionDetail {
  return {
    session: session(id),
    devices: [],
    signals,
    writes: [],
    controllers: [],
    events: [],
    spans: [],
    startS: 0,
  };
}

/**
 * A transport serving one session's metadata and, for `series`, whatever
 * `points` says for that address -- logging every request's path so a test
 * can assert what was (and was not) fetched. Every test below uses its own
 * session id: `useSession.ts`'s series cache is module-level (by design --
 * that's what lets two mounts of one signal share a fetch), so two tests
 * sharing an id would see each other's fetches instead of their own.
 */
function fakeTransport(id: number, points: Record<string, Array<{ offset_ns: number; value: unknown }>>, log: string[]): Transport {
  return {
    async request({ method, path }: Request): Promise<Response> {
      log.push(`${method} ${path}`);
      const seriesMatch = new RegExp(`^/api/history/sessions/${id}/series/([^/]+)$`).exec(path);
      if (seriesMatch) {
        const address = decodeURIComponent(seriesMatch[1]!);
        return { status: 200, json: { signal: signalRow(address, ""), points: points[address] ?? [], downsample: null } };
      }
      if (path === `/api/history/sessions/${id}`) return { status: 200, json: session(id) };
      if (path.endsWith("/devices")) return { status: 200, json: [] };
      if (path.endsWith("/signals")) return { status: 200, json: [] };
      if (path.endsWith("/writes")) return { status: 200, json: [] };
      if (path.endsWith("/controllers")) return { status: 200, json: [] };
      if (path.endsWith("/events")) return { status: 200, json: [] };
      if (path.endsWith("/spans")) return { status: 200, json: [] };
      return { status: 404, json: undefined };
    },
    stream(_path: string, _handlers: StreamHandlers): Subscription {
      return { close: () => undefined };
    },
  };
}

function withRig(transport: Transport, children: React.ReactNode) {
  return createElement(RigProvider, { transport }, children);
}

beforeEach(() => {
  observed = [];
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SessionPanel", () => {
  it("renders the latest numeric trace value once its group is shown", async () => {
    const log: string[] = [];
    const detail = shell(101, [signalRow("furnace.zone1", "°C")]);
    const points = { "furnace.zone1": [0, 1, 2].map((i) => ({ offset_ns: i * 1e9, value: [1, 2, 3.456][i]! })) };
    render(withRig(fakeTransport(101, points, log), createElement(SessionPanel, { detail })));

    const host = document.querySelector(".fb-channel")!;
    act(() => fireVisible(host));
    await waitFor(() => expect(screen.getByText(/3\.46/)).toBeTruthy());
  });

  // A generator-based controller write (a ramp `regulate`, say) records a non-numeric
  // value -- an object, not a number -- for its target signal. The panel used to call
  // `fixed()` on it unguarded and crash the whole page (`e.toFixed is not a function`).
  it("does not crash on a non-numeric latest trace value, and shows the same fallback as ControllerPanel's value()/describeReference", async () => {
    const log: string[] = [];
    const detail = shell(102, [signalRow("furnace.zone1", "°C")]);
    const points = { "furnace.zone1": [0, 1, 2].map((i) => ({ offset_ns: i * 1e9, value: [1, 2, { tag: "regulate" }][i]! })) };
    render(withRig(fakeTransport(102, points, log), createElement(SessionPanel, { detail })));

    const host = document.querySelector(".fb-channel")!;
    expect(() => act(() => fireVisible(host))).not.toThrow();
    await waitFor(() => expect(screen.getByText("?", { exact: false })).toBeTruthy());
  });

  it("resolves the shell (via useSession) without ever asking for a series -- Sessions.tsx's own drill-in path", async () => {
    // useSession itself, not the panel: the shell must not touch /series regardless of
    // what (if anything) later mounts to look at it.
    const { useSession } = await import("../src/hooks/useSession.js");
    const { renderHook, waitFor: waitForHook } = await import("@testing-library/react");
    const log: string[] = [];
    const points = {};
    const result = renderHook(() => useSession(103), { wrapper: ({ children }) => withRig(fakeTransport(103, points, log), children) as React.ReactElement });
    await waitForHook(() => expect(result.result.current.data).toBeTruthy());
    expect(log.some((l) => l.includes("/series/"))).toBe(false);
    expect(log.some((l) => l.includes("/ticks/"))).toBe(false);
  });

  it("fetches a group's series only once it is shown, and not again on a second showing", async () => {
    const log: string[] = [];
    const detail = shell(104, [signalRow("furnace.zone1", "°C")]);
    const points = { "furnace.zone1": [{ offset_ns: 0, value: 1 }] };
    const { unmount } = render(withRig(fakeTransport(104, points, log), createElement(SessionPanel, { detail })));

    // Mounted, nothing shown yet: no series request.
    expect(log.some((l) => l.includes("/series/"))).toBe(false);

    const host = document.querySelector(".fb-channel")!;
    act(() => fireVisible(host));
    await waitFor(() => expect(log.filter((l) => l.includes("/series/")).length).toBe(1));

    unmount();
    // A second mount of the same session/signal (switching grouping and back, in the real
    // page) is served from the module-level cache in useSession.ts: no second request.
    render(withRig(fakeTransport(104, points, log), createElement(SessionPanel, { detail })));
    const host2 = document.querySelector(".fb-channel")!;
    act(() => fireVisible(host2));
    await waitFor(() => expect(screen.getByText(/1 pts/)).toBeTruthy());
    expect(log.filter((l) => l.includes("/series/")).length).toBe(1);
  });

  it("never fetches a series for a non-chartable signal (enum/json dtype, or a device's own housekeeping), and lists it plainly instead", async () => {
    const log: string[] = [];
    const detail = shell(105, [
      signalRow("furnace.zone1", "°C"),
      signalRow("blender.mode", "", { dtype: "enum" }),
      signalRow("blender.conditions", "", { dtype: "json" }),
      signalRow("blender.last.set_blend", "", { dtype: "json" }),
    ]);
    const points = { "furnace.zone1": [{ offset_ns: 0, value: 1 }] };
    render(withRig(fakeTransport(105, points, log), createElement(SessionPanel, { detail })));

    for (const el of document.querySelectorAll(".fb-channel")) act(() => fireVisible(el));
    await waitFor(() => expect(log.some((l) => l.includes("/series/furnace.zone1"))).toBe(true));

    expect(log.some((l) => l.includes("/series/blender.mode"))).toBe(false);
    expect(log.some((l) => l.includes("/series/blender.conditions"))).toBe(false);
    expect(log.some((l) => l.includes("/series/blender.last.set_blend"))).toBe(false);
    expect(screen.getByText(/Recorded, not charted/)).toBeTruthy();
  });
});
