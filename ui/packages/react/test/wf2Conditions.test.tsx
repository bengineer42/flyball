import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { atLeast, describeDuration, describeEdge, severityRank, type RigEvent } from "@flyball/client";
import { EventsPanel, eventKey } from "../src/panels/EventsPanel.js";

const event = (over: Partial<RigEvent>): RigEvent => ({
  time_ns: 1_000_000_000,
  severity: "warning",
  scope: "device",
  subject: "furnace",
  code: "slow",
  message: "read took 2 s against a 1 s period",
  details: null,
  edge: null,
  ...over,
});

describe("severity", () => {
  it("ranks as logging does and compares by rank, not as strings", () => {
    expect(["debug", "info", "warning", "error"].map((s) => severityRank(s as RigEvent["severity"]))).toEqual([10, 20, 30, 40]);
    expect(atLeast("error", "warning")).toBe(true);
    expect(atLeast("info", "warning")).toBe(false);
  });
});

describe("condition edges", () => {
  it("words a cleared edge with how long the condition held", () => {
    expect(describeEdge("raised", null)).toBe("raised");
    expect(describeEdge("cleared", { duration_s: 12.4 })).toBe("cleared after 12 s");
    expect(describeEdge("cleared", { duration_s: 185 })).toBe("cleared after 3 min 5 s");
    expect(describeEdge("cleared", null)).toBe("cleared");
    expect(describeEdge(null, null)).toBeNull();
    expect(describeDuration(0.25)).toBe("250 ms");
  });

  it("shows raised and cleared beside the code in the events table", () => {
    const raised = event({ edge: "raised" });
    const cleared = event({ time_ns: 13_000_000_000, severity: "info", edge: "cleared", message: "reads keep up again", details: { duration_s: 12 } });
    const html = renderToStaticMarkup(<EventsPanel events={[raised, cleared, event({ code: "restored", scope: "rig" })]} nowS={20} />);
    expect(html).toContain("raised");
    expect(html).toContain("cleared after 12 s");
    expect(html.match(/data-testid="event-edge"/g)).toHaveLength(2);
  });

  it("keys a raised and a cleared edge at the same instant apart", () => {
    expect(eventKey(event({ edge: "raised" }))).not.toBe(eventKey(event({ edge: "cleared" })));
  });
});
