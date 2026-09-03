/**
 * Driving the pumps by hand.
 *
 * Three ways in, matching the three the rig accepts: blend (ratio and total),
 * absolute flows, and raw efforts. Every one of them suspends a running
 * controller, which is stated on the panel rather than discovered afterwards.
 */

import { useState } from "react";

import type { PumpsSpec, RigState } from "../api/types";
import { flowsFromBlend, maxFlowAt, resolveFlow } from "../domain/flows";
import { formatQuantity } from "../domain/units";
import { DEFAULT_FLOW } from "../programs/steps";
import type { RigHandle } from "../state/useRig";
import { FlowEditor } from "./editors";
import { Card, Note, NumberField, Slider } from "./primitives";

type Mode = "blend" | "flows" | "efforts";

export interface PumpControlsProps {
  rig: RigHandle;
  state: RigState | null;
  spec: PumpsSpec | null | undefined;
  confirm: boolean;
}

export function PumpControls({ rig, state, spec, confirm }: PumpControlsProps) {
  const [mode, setMode] = useState<Mode>("blend");
  const [wetFraction, setWetFraction] = useState(0.5);
  const [flow, setFlow] = useState(DEFAULT_FLOW);
  const [flows, setFlows] = useState({ wet: 0, dry: 0 });
  const [efforts, setEfforts] = useState({ wet: 0, dry: 0 });

  const busy = rig.command.pending !== null;
  const units = spec?.units ?? null;
  const ask = (message: string) => !confirm || window.confirm(message);
  const controllerRunning = Boolean(state?.controller && !state.controller.suspended);

  const preview = spec ? flowsFromBlend(resolveFlow(flow, wetFraction, spec), wetFraction) : null;

  return (
    <Card
      title="Manual pump control"
      hint="suspends the controller"
      actions={
        <button
          type="button"
          className="danger small"
          disabled={busy}
          onClick={() => {
            if (!ask("Stop both pumps?")) return;
            void rig.run("Stop pumps", (transport) => transport.stopPumps());
          }}
        >
          Stop pumps
        </button>
      }
    >
      <div className="stack tight">
        {controllerRunning && (
          <Note tone="warning">
            A controller is regulating. Any command here takes the pumps off it until you resume.
          </Note>
        )}

        <div className="row" role="tablist" aria-label="Control mode">
          {(["blend", "flows", "efforts"] as Mode[]).map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={mode === option}
              className={mode === option ? "" : "ghost"}
              onClick={() => setMode(option)}
            >
              {option === "blend" ? "Blend" : option === "flows" ? "Flows" : "Efforts"}
            </button>
          ))}
        </div>

        {mode === "blend" && (
          <>
            <Slider
              label="Wet fraction"
              min={0}
              max={1}
              step={0.01}
              value={wetFraction}
              onChange={setWetFraction}
              format={(value) => `${(value * 100).toFixed(0)}% wet`}
            />
            <FlowEditor value={flow} onChange={(next) => setFlow(next ?? DEFAULT_FLOW)} spec={spec} wetFraction={wetFraction} />
            {preview && (
              <p className="hint" style={{ margin: 0 }}>
                Sends wet <span className="mono">{formatQuantity(preview.wet, units)}</span>, dry{" "}
                <span className="mono">{formatQuantity(preview.dry, units)}</span>
                {spec && ` — the blend can reach ${formatQuantity(maxFlowAt(spec, wetFraction), units)}`}
              </p>
            )}
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => {
                if (!ask(`Set the blend to ${(wetFraction * 100).toFixed(0)}% wet?`)) return;
                void rig.run("Set blend", (transport) => transport.setBlend(flow, wetFraction));
              }}
            >
              Apply blend
            </button>
          </>
        )}

        {mode === "flows" && (
          <>
            <div className="grid cols-2" style={{ gap: 8 }}>
              <NumberField
                label="Wet flow"
                unit={units ?? undefined}
                min={0}
                max={spec?.max_flows.wet}
                step={0.01}
                value={flows.wet}
                onChange={(value) => setFlows((current) => ({ ...current, wet: value ?? 0 }))}
              />
              <NumberField
                label="Dry flow"
                unit={units ?? undefined}
                min={0}
                max={spec?.max_flows.dry}
                step={0.01}
                value={flows.dry}
                onChange={(value) => setFlows((current) => ({ ...current, dry: value ?? 0 }))}
              />
            </div>
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => {
                if (!ask(`Set flows to wet ${flows.wet}, dry ${flows.dry}?`)) return;
                void rig.run("Set flows", (transport) => transport.setFlows(flows.wet, flows.dry));
              }}
            >
              Apply flows
            </button>
          </>
        )}

        {mode === "efforts" && (
          <>
            <Slider
              label="Wet effort"
              min={0}
              max={1}
              step={0.01}
              value={efforts.wet}
              onChange={(wet) => setEfforts((current) => ({ ...current, wet }))}
              format={(value) => `${(value * 100).toFixed(0)}%`}
            />
            <Slider
              label="Dry effort"
              min={0}
              max={1}
              step={0.01}
              value={efforts.dry}
              onChange={(dry) => setEfforts((current) => ({ ...current, dry }))}
              format={(value) => `${(value * 100).toFixed(0)}%`}
            />
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => {
                if (!ask("Set pump efforts?")) return;
                void rig.run("Set efforts", (transport) =>
                  transport.setEfforts(efforts.wet, efforts.dry),
                );
              }}
            >
              Apply efforts
            </button>
          </>
        )}
      </div>
    </Card>
  );
}
