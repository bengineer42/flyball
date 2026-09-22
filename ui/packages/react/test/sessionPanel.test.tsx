import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { SessionRow, SignalRow } from "@flyball/client";
import { SessionPanel } from "../src/panels/SessionPanel.js";
import type { SessionDetail } from "../src/hooks/useSession.js";

/** A recorded signal row, minimal but complete. */
function signalRow(address: string, unit: string): SignalRow {
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
  };
}

function session(): SessionRow {
  return {
    id: 1,
    start_ns: 0,
    end_ns: 60_000_000_000,
    version: null,
    config: null,
    hardware: null,
    details: null,
  };
}

/** A minimal `SessionDetail` with one trace, so `SessionPanel` renders standalone. */
function detail(v: number[]): SessionDetail {
  const signal = signalRow("furnace.zone1", "°C");
  return {
    session: session(),
    devices: [],
    signals: [signal],
    writes: [],
    controllers: [],
    events: [],
    spans: [],
    traces: { "furnace.zone1": { signal, unit: signal.unit, t: v.map((_, i) => i), v } },
    ticks: {},
    startS: 0,
  };
}

describe("SessionPanel", () => {
  it("renders the latest numeric trace value", () => {
    const html = renderToStaticMarkup(<SessionPanel detail={detail([1, 2, 3.456])} />);
    expect(html).toContain("3.46");
  });

  // A generator-based controller write (a ramp `regulate`, say) records a non-numeric
  // value -- an object, not a number -- for its target signal. The panel used to call
  // `fixed()` on it unguarded and crash the whole page (`e.toFixed is not a function`).
  it("does not crash on a non-numeric latest trace value, and shows the same fallback as ControllerPanel's value()/describeReference", () => {
    const bad = detail([1, 2, { tag: "regulate" } as unknown as number]);
    expect(() => renderToStaticMarkup(<SessionPanel detail={bad} />)).not.toThrow();
    const html = renderToStaticMarkup(<SessionPanel detail={bad} />);
    expect(html).toContain("?");
  });
});
