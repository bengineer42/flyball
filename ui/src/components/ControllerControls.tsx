/**
 * Starting, steering and handing back the controller.
 *
 * The set point is applied on submit rather than as the number is typed: a
 * slider that regulates a real chamber on every intermediate value is a way to
 * drive the pumps somewhere nobody asked for.
 */

import { useEffect, useState } from "react";

import type { FlowRequest, LawConfig, PumpsSpec, RigState } from "../api/types";
import type { RigHandle } from "../state/useRig";
import { DEFAULT_FLOW, DEFAULT_LAW } from "../programs/steps";
import { FlowEditor, LawEditor } from "./editors";
import { Card, NumberField, Note } from "./primitives";

export interface ControllerControlsProps {
  rig: RigHandle;
  state: RigState | null;
  spec: PumpsSpec | null | undefined;
  confirm: boolean;
}

export function ControllerControls({ rig, state, spec, confirm }: ControllerControlsProps) {
  const controller = state?.controller ?? null;
  const [setPoint, setSetPoint] = useState(50);
  const [flow, setFlow] = useState<FlowRequest | null>({ ...DEFAULT_FLOW });
  const [law, setLaw] = useState<LawConfig | null>({ ...DEFAULT_LAW });

  // Adopt the rig's set point when a controller appears, so the field never
  // sits on a stale number the operator might submit by accident.
  useEffect(() => {
    if (controller) setSetPoint(controller.set_point);
  }, [controller?.set_point]);

  const ask = (message: string) => !confirm || window.confirm(message);
  const busy = rig.command.pending !== null;

  if (!controller) {
    return (
      <Card title="Start controller" hint="begins regulating immediately">
        <div className="stack tight">
          <NumberField
            label="Set point"
            unit="%"
            min={0}
            max={100}
            step={0.1}
            value={setPoint}
            onChange={(value) => setSetPoint(value ?? 0)}
          />
          <FlowEditor value={flow} onChange={setFlow} optional spec={spec} />
          <LawEditor value={law} onChange={setLaw} optional />
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => {
              if (!ask(`Start regulating to ${setPoint}%? The pumps will run.`)) return;
              void rig.run("Start controller", (transport) =>
                transport.startController({ humidity: setPoint, flow, control_law: law }),
              );
            }}
          >
            Start controller
          </button>
        </div>
      </Card>
    );
  }

  return (
    <Card
      title="Controller"
      hint={controller.suspended ? "suspended" : "regulating"}
      actions={
        <button
          type="button"
          className="danger small"
          disabled={busy}
          onClick={() => {
            if (!ask("Stop the loop and the pumps?")) return;
            void rig.run("Stop rig", (transport) => transport.stopRig());
          }}
        >
          Stop rig
        </button>
      }
    >
      <div className="stack tight">
        <NumberField
          label="Set point"
          unit="%"
          min={0}
          max={100}
          step={0.1}
          value={setPoint}
          onChange={(value) => setSetPoint(value ?? 0)}
        />
        <div className="row">
          <button
            type="button"
            className="primary"
            disabled={busy || setPoint === controller.set_point}
            onClick={() => {
              if (!ask(`Move the set point to ${setPoint}%?`)) return;
              void rig.run("Set point", (transport) => transport.setSetPoint(setPoint));
            }}
          >
            Apply set point
          </button>
          {controller.suspended ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (!ask("Hand the pumps back to the controller?")) return;
                void rig.run("Resume controller", (transport) => transport.resumeController());
              }}
            >
              Resume control
            </button>
          ) : (
            <button
              type="button"
              disabled={busy || !rig.capabilities.suspend}
              title={
                rig.capabilities.suspend
                  ? undefined
                  : "This daemon does not expose suspend (R3). Driving the pumps directly suspends the controller as a side effect."
              }
              onClick={() => {
                if (!ask("Suspend the controller? The pumps hold their current output.")) return;
                void rig.run("Suspend controller", (transport) => transport.suspendController());
              }}
            >
              Suspend
            </button>
          )}
        </div>
        {controller.suspended && (
          <Note tone="warning">
            The controller is suspended and the pumps are wherever manual control left them.
            Resuming re-enters control without stepping the output; a proportional law cannot hold
            the offset and will bump.
          </Note>
        )}
      </div>
    </Card>
  );
}
