/** Rig edits carry `base`/`force` as query parameters and answer `RigEditOut`; the stop/latch/reset routes; quality words. */
import { describe, expect, it } from "vitest";
import { describeCaveats, describeEventCode, describeQuality, RigClient, type Request, type Response, type Transport } from "@flyball/client";

function recording(): { transport: Transport; asked: Request[] } {
  const asked: Request[] = [];
  const transport: Transport = {
    base: "",
    async request(r: Request): Promise<Response> {
      asked.push(r);
      return { status: 202, json: { version: 7, previous: 6, reason: "edited", saved: null, restarting: true, stop: null, detail: "" } };
    },
    stream: () => ({ close: () => undefined }),
  };
  return { transport, asked };
}

describe("rig edits", () => {
  it("send base and force only when given, and answer the edit", async () => {
    const { transport, asked } = recording();
    const rig = new RigClient(transport);
    expect((await rig.removeDevice("probe")).version).toBe(7);
    await rig.addLink({ name: "t1", type: "sim_plant" }, { base: 6, force: true });
    await rig.restoreVersion(3, { force: true });
    await rig.resetRig("on_fault:heaters.heater1");
    await rig.latches();
    await rig.stopPlan();
    expect(asked.map((r) => [r.method, r.path, r.query, r.body])).toEqual([
      ["DELETE", "/api/devices/probe", undefined, undefined],
      ["POST", "/api/links", { base: 6, force: true }, { name: "t1", type: "sim_plant" }],
      ["POST", "/api/rig/versions/3/restore", { force: true }, undefined],
      ["POST", "/api/rig/reset", undefined, { cause: "on_fault:heaters.heater1" }],
      ["GET", "/api/rig/latches", undefined, undefined],
      ["GET", "/api/rig/stop", undefined, undefined],
    ]);
  });
});

describe("words", () => {
  it("say why a reading has no value", () => {
    expect(describeQuality("stale", "device_offline")).toBe("stale: device offline");
    expect(describeQuality("invalid", "sensor open")).toBe("invalid: sensor open");
    expect(describeQuality("not_applicable")).toBe("n/a");
    expect(describeQuality("ok")).toBe("");
    expect(describeQuality(undefined)).toBe("");
    expect(describeCaveats({ at_limit: "high", out_of_range: "low" })).toBe("at the high limit, below range");
  });
  it("label the new codes", () => {
    expect(describeEventCode("edit_not_built")).toBe("Edit could not be built");
    expect(describeEventCode("write_dropped")).toBe("Kept value dropped");
    expect(describeEventCode("commit_failed")).toBe("Commit failed"); // gone from the table: humanised
  });
});
