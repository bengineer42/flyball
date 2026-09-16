import { describe, expect, it } from "vitest";
import { RigClient, RigError, addressOf, captionFor, describeUnit, deviceOf, deviceTitle, isNamespace, placeOf, publishes, signalsOf, titleFor, unitTitle, withUnit, writable, type ReadOut, type Request, type Transport, type TreeNode } from "@flyball/client";

/** A transport answering from a table of `METHOD path` → body, recording what was asked. */
function fakeTransport(routes: Record<string, unknown>, status = 200) {
  const asked: Request[] = [];
  const transport: Transport = {
    base: "",
    async request(request) {
      asked.push(request);
      const key = `${request.method} ${request.path}`;
      if (!(key in routes)) return { status: 404, json: { detail: `${key} not found` } };
      return { status, json: routes[key] };
    },
    stream: () => ({ close: () => undefined }),
  };
  return { transport, asked };
}

const reading = { reading: { signal: "furnace.zone1", time_ns: 1e9, value: 20 } };
const sample = { sample: { node: "hum_sensors.dry", time_ns: 1e9, values: { humidity: 4.1, temperature: 21.9 } } };
const samples = { samples: [{ node: "furnace", time_ns: 1e9, values: { zone1: 20, zone2: 21 } }] };

/** What a caller does with a `ReadOut`: narrow on the one key that is present. */
function kindOf(result: ReadOut): "reading" | "sample" | "samples" {
  if ("reading" in result) return "reading";
  if ("sample" in result) return "sample";
  return "samples";
}

describe("RigClient.read", () => {
  it("answers a signal with a reading, an atomic namespace with a sample, a device with samples", async () => {
    const { transport, asked } = fakeTransport({
      "GET /api/read/furnace.zone1": reading,
      "GET /api/read/hum_sensors.dry": sample,
      "GET /api/read/furnace": samples,
    });
    const rig = new RigClient(transport);
    const one = await rig.read("furnace.zone1");
    const node = await rig.read("hum_sensors.dry");
    const device = await rig.read("furnace");
    expect(kindOf(one)).toBe("reading");
    expect(kindOf(node)).toBe("sample");
    expect(kindOf(device)).toBe("samples");
    if ("reading" in one) expect(one.reading.value).toBe(20);
    if ("sample" in node) expect(node.sample.values.humidity).toBe(4.1);
    if ("samples" in device) expect(device.samples[0]!.values).toEqual({ zone1: 20, zone2: 21 });
    // The three shapes never share a key, so `in` tells them apart with nothing else present.
    expect(Object.keys(one)).toEqual(["reading"]);
    expect(Object.keys(node)).toEqual(["sample"]);
    expect(Object.keys(device)).toEqual(["samples"]);
    expect(asked.map((r) => r.query)).toEqual([undefined, undefined, undefined]);
  });

  it("reads several addresses at once, in order, and passes fresh through", async () => {
    const { transport, asked } = fakeTransport({ "GET /api/read": [reading, sample] });
    const rig = new RigClient(transport);
    const results = await rig.read(["furnace.zone1", "hum_sensors.dry"], true);
    expect(results.map(kindOf)).toEqual(["reading", "sample"]);
    expect(asked[0]!.query).toEqual({ at: "furnace.zone1,hum_sensors.dry", fresh: true });
    await rig.read("furnace.zone1", true).catch(() => undefined);
    expect(asked[1]!.path).toBe("/api/read/furnace.zone1");
    expect(asked[1]!.query).toEqual({ fresh: true });
  });

  it("raises the server's detail as a RigError", async () => {
    const { transport } = fakeTransport({});
    const rig = new RigClient(transport);
    await expect(rig.read("nowhere.x")).rejects.toMatchObject({ status: 404, detail: "GET /api/read/nowhere.x not found" });
    await expect(rig.read("nowhere.x")).rejects.toBeInstanceOf(RigError);
  });
});

describe("RigClient routes by address", () => {
  it("puts a demand on a signal and on a device, and drives a controller by its target", async () => {
    const write = { value: 500, requested: null, at_limit: null, controller: null };
    const { transport, asked } = fakeTransport({
      "PUT /api/signals/heaters.heater1": { "heaters.heater1": write },
      "PUT /api/devices/heaters/demand": { "heaters.heater1": write, "heaters.heater2": write },
      "POST /api/controllers/heaters.heater1/regulate": {},
      "PUT /api/controllers/heaters.heater1/reference": {},
      "POST /api/devices/furnace/commands/fail": null,
      "POST /api/waits/step-1/fire": { name: "step-1", fired: true },
    });
    const rig = new RigClient(transport);
    expect(await rig.demand("heaters.heater1", 500)).toEqual({ "heaters.heater1": write });
    expect(Object.keys(await rig.demandNode("heaters", { heater1: 500, heater2: 500 }))).toEqual(["heaters.heater1", "heaters.heater2"]);
    await rig.regulate("heaters.heater1", { at: 100 });
    await rig.setReference("heaters.heater1", "process");
    await rig.command("furnace", "fail", { signal: "zone1" });
    expect(await rig.fireWait("step-1")).toEqual({ name: "step-1", fired: true });
    expect(asked.map((r) => [r.method, r.path, r.body])).toEqual([
      ["PUT", "/api/signals/heaters.heater1", 500],
      ["PUT", "/api/devices/heaters/demand", { heater1: 500, heater2: 500 }],
      ["POST", "/api/controllers/heaters.heater1/regulate", { at: 100 }],
      ["PUT", "/api/controllers/heaters.heater1/reference", { at: "process" }],
      ["POST", "/api/devices/furnace/commands/fail", { signal: "zone1" }],
      ["POST", "/api/waits/step-1/fire", undefined],
    ]);
  });

  it("builds history routes and export URLs by address", async () => {
    const { transport, asked } = fakeTransport({
      "GET /api/history/sessions/3/series/furnace.zone1": { signal: {}, points: [], downsample: null },
      "GET /api/history/sessions/3/writes/heaters.heater1": [],
      "GET /api/history/sessions/3/ticks/heaters.heater1": [],
    });
    const rig = new RigClient(transport);
    await rig.series(3, "furnace.zone1", { max_points: 10 });
    await rig.writeStates(3, "heaters.heater1", { start_ns: 5 });
    await rig.ticks(3, "heaters.heater1", { every: 2 });
    expect(asked.map((r) => [r.path, r.query])).toEqual([
      ["/api/history/sessions/3/series/furnace.zone1", { max_points: 10 }],
      ["/api/history/sessions/3/writes/heaters.heater1", { start_ns: 5 }],
      ["/api/history/sessions/3/ticks/heaters.heater1", { every: 2 }],
    ]);
    expect(rig.seriesExportUrl(3, "furnace.zone1", "json")).toBe("/api/history/sessions/3/series/furnace.zone1/export?format=json");
    expect(rig.writesExportUrl(3, "heaters.heater1")).toBe("/api/history/sessions/3/writes/heaters.heater1/export?format=csv");
    expect(rig.ticksExportUrl(3, "heaters.heater1")).toBe("/api/history/sessions/3/ticks/heaters.heater1/export?format=csv");
    expect(rig.exportUrl(3, { format: "zip" })).toBe("/api/history/sessions/3/export?format=zip");
  });
});

describe("address helpers", () => {
  it("joins and splits addresses", () => {
    expect(addressOf("hum_sensors.dry", "humidity")).toBe("hum_sensors.dry.humidity");
    expect(deviceOf("hum_sensors.dry.humidity")).toBe("hum_sensors");
    expect(deviceOf("furnace")).toBe("furnace");
  });

  it("flattens a device tree to its signals, namespaces recursed", () => {
    const signal = (address: string, access: string): TreeNode =>
      ({ name: address.split(".").pop()!, address, access, label: "", quantity: "q", unit: "", dimension: null, dtype: "float", shape: [], range: null, precision: null, warn: null, alarm: null, poll_s: null, limits: null, role: "output", tags: {}, initial: null, latest: null, write: null }) as TreeNode;
    const tree: TreeNode[] = [
      signal("d.a", "rp"),
      { name: "ns", address: "d.ns", atomic: true, label: "", poll_s: null, signals: [signal("d.ns.b", "w"), signal("d.ns.c", "rw")] },
    ];
    expect(isNamespace(tree[1]!)).toBe(true);
    expect(isNamespace(tree[0]!)).toBe(false);
    const flat = signalsOf(tree);
    expect(flat.map((s) => s.address)).toEqual(["d.a", "d.ns.b", "d.ns.c"]);
    expect(flat.map(publishes)).toEqual([true, false, false]);
    expect(flat.map(writable)).toEqual([false, true, true]);
  });
});

describe("titles from labels", () => {
  const signal = (address: string, unit: string, quantity = "q", label = ""): TreeNode =>
    ({ name: address.split(".").pop()!, address, access: "rp", label, quantity, unit, dimension: null, dtype: "float", shape: [], range: null, precision: null, warn: null, alarm: null, poll_s: null, limits: null, role: "output", tags: {}, initial: null, latest: null, write: null }) as TreeNode;
  const sensors = {
    name: "hum_sensors",
    label: "Humidity sensors",
    signals: [{ name: "chamber", address: "hum_sensors.chamber", atomic: true, label: "", poll_s: null, signals: [signal("hum_sensors.chamber.humidity", "%RH", "humidity"), signal("hum_sensors.chamber.temperature", "°C", "temperature")] }] as TreeNode[],
  };
  const blender = { name: "blender", label: null, signals: [signal("blender.expected_humidity", "%RH", "humidity"), signal("blender.dry_effort", "1", "effort")] };
  const devices = [sensors, blender];

  it("never prints the dimensionless unit `1`, and keeps a worded one", () => {
    expect(describeUnit("1")).toBe("");
    expect(describeUnit("")).toBe("");
    expect(describeUnit("of full")).toBe("of full");
    expect(withUnit("0.25", "1")).toBe("0.25");
    expect(withUnit("0.25", "%RH")).toBe("0.25 %RH");
  });

  it("finds a signal's device and innermost namespace", () => {
    const place = placeOf("hum_sensors.chamber.humidity", devices);
    expect(place.device?.name).toBe("hum_sensors");
    expect(place.namespace?.address).toBe("hum_sensors.chamber");
    expect(placeOf("blender.dry_effort", devices)).toEqual({ device: blender });
    expect(placeOf("nowhere.x", devices)).toEqual({});
  });

  it("titles by namespace, then by device, never by address", () => {
    const chamber = signalsOf(sensors.signals)[0]!;
    const expected = blender.signals[0] as Extract<TreeNode, { access: string }>;
    expect(titleFor(chamber, placeOf(chamber.address, devices))).toBe("Chamber humidity");
    expect(titleFor(expected, placeOf(expected.address, devices))).toBe("Expected humidity · Blender");
    expect(titleFor(expected)).toBe("Expected humidity");
    expect(titleFor(signal("d.rh", "%RH", "humidity", "RH"), { namespace: { name: "wet", address: "d.wet", label: "" } })).toBe("Wet RH");
    expect(captionFor(placeOf(chamber.address, devices))).toBe("Chamber · Humidity sensors");
    expect(deviceTitle(blender)).toBe("Blender");
  });

  it("heads a unit chart by the unit, or the quantities when the unit is dimensionless", () => {
    expect(unitTitle("%RH", [])).toBe("%RH");
    expect(unitTitle("1", [signal("b.dry_effort", "1", "effort"), signal("b.wet_effort", "1", "effort")] as Array<{ quantity: string }>)).toBe("Effort");
    expect(unitTitle("1", [])).toBe("dimensionless");
  });
});
