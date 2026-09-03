/**
 * Hands on the rig.
 *
 * Read-outs sit beside the controls that change them, so the effect of a
 * command is visible on the same screen it was issued from.
 */

import type { RigState } from "../api/types";
import { lineStatus } from "../domain/readings";
import type { RigHandle } from "../state/useRig";
import { ControllerControls } from "../components/ControllerControls";
import { ControllerStatus } from "../components/ControllerStatus";
import { PumpControls } from "../components/PumpControls";
import { PumpsStatus } from "../components/PumpsStatus";
import { ReadingsPanel } from "../components/ReadingsPanel";
import { RecordingControls } from "../components/RecordingControls";

export interface ControlProps {
  rig: RigHandle;
  state: RigState | null;
  confirm: boolean;
}

export function Control({ rig, state, confirm }: ControlProps) {
  const spec = state?.pumps_spec ?? null;
  const process = lineStatus(state?.readings, "process");

  return (
    <div className="stack">
      <div className="grid cols-2">
        <ControllerStatus
          controller={state?.controller}
          processHumidity={process.kind === "reading" ? process.reading.humidity : null}
        />
        <PumpsStatus
          pumps={state?.pumps}
          spec={spec}
          expectedHumidity={state?.expected_humidity}
          measured={state?.measured_flows}
        />
      </div>

      <div className="grid cols-2">
        <ControllerControls rig={rig} state={state} spec={spec} confirm={confirm} />
        <div className="stack">
          <PumpControls rig={rig} state={state} spec={spec} confirm={confirm} />
          <RecordingControls rig={rig} state={state} />
        </div>
      </div>

      <ReadingsPanel readings={state?.readings} />
    </div>
  );
}
