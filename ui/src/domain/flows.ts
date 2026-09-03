/**
 * Blend maths, ported from `humctrl.pumps.types` and `humctrl.controller`.
 *
 * The UI needs these to preview what a request will do *before* sending it —
 * "0.6 of blend max at 40% wet is 1.2 L/min" — so an operator is not typing
 * blind into a rig that will reject the value. The daemon remains the
 * authority: this only predicts, it never substitutes for the rig's own check.
 */

import type { Efforts, Flows, FlowRequest, Normalised, Percent, PumpsSpec } from "../api/types";

export function totalFlow(flows: Flows): number {
  return flows.wet + flows.dry;
}

export function wetFraction(flows: Flows): Normalised {
  const total = totalFlow(flows);
  return total > 0 ? flows.wet / total : 0;
}

export function flowsFromBlend(total: number, wet: Normalised): Flows {
  return { wet: total * wet, dry: total * (1 - wet) };
}

export function flowsToEfforts(flows: Flows, spec: PumpsSpec): Efforts {
  return {
    wet: flows.wet / spec.max_flows.wet,
    dry: flows.dry / spec.max_flows.dry,
  };
}

export function effortsToFlows(efforts: Efforts, spec: PumpsSpec): Flows {
  return {
    wet: efforts.wet * spec.max_flows.wet,
    dry: efforts.dry * spec.max_flows.dry,
  };
}

export function overdriven(efforts: Efforts): boolean {
  return efforts.wet > 1 || efforts.dry > 1;
}

/** The most total flow this rig can deliver at a given blend ratio. */
export function maxFlowAt(spec: PumpsSpec, wet: Normalised): number {
  if (wet <= 0) return spec.max_flows.dry;
  if (wet >= 1) return spec.max_flows.wet;
  return Math.min(spec.max_flows.dry / (1 - wet), spec.max_flows.wet / wet);
}

/** Resolve a FlowRequest to absolute flow, the way DualPumps.set_blend does. */
export function resolveFlow(
  request: FlowRequest,
  wet: Normalised,
  spec: PumpsSpec,
): number {
  switch (request.scale) {
    case "absolute":
      return request.value;
    case "blend_max":
      return request.value * maxFlowAt(spec, wet);
    case "full_range_max":
      return request.value * spec.full_range_max_flow;
  }
}

/** Humidity a blend should settle at, given the two source lines. */
export function expectedHumidityFromFraction(
  dry: Percent,
  wetHumidity: Percent,
  fraction: Normalised,
): Percent {
  return dry + fraction * (wetHumidity - dry);
}

export type WetFractionResult =
  | { kind: "fraction"; value: Normalised }
  | { kind: "railed"; rail: "wet" | "dry" }
  | { kind: "impossible"; reason: string };

/**
 * The blend that would hold `target`, or which rail the request sits past.
 * Mirrors `controller.calculate_wet_fraction`, including its refusal when the
 * wet line is no wetter than the dry one.
 */
export function calculateWetFraction(
  dry: Percent,
  wet: Percent,
  target: Percent,
): WetFractionResult {
  if (wet <= dry) {
    return {
      kind: "impossible",
      reason: `Wet line (${wet.toFixed(1)}%) is not wetter than the dry line (${dry.toFixed(1)}%)`,
    };
  }
  if (target < dry) return { kind: "railed", rail: "dry" };
  if (target > wet) return { kind: "railed", rail: "wet" };
  return { kind: "fraction", value: (target - dry) / (wet - dry) };
}

export const FLOW_SCALE_LABELS: Record<FlowRequest["scale"], string> = {
  absolute: "absolute",
  blend_max: "of blend max",
  full_range_max: "of full-range max",
};
