/**
 * The record.
 *
 * Flags are the only way to mark an instant in a run from the UI, so the field
 * stays available while recording and clears after each one.
 */

import { useState } from "react";

import type { RigState } from "../api/types";
import type { RigHandle } from "../state/useRig";
import { Badge, Card, TextField } from "./primitives";

export interface RecordingControlsProps {
  rig: RigHandle;
  state: RigState | null;
}

export function RecordingControls({ rig, state }: RecordingControlsProps) {
  const [flag, setFlag] = useState("");
  const recording = Boolean(state?.recording);
  const busy = rig.command.pending !== null;

  return (
    <Card
      title="Recording"
      actions={recording ? <Badge tone="good" dot>recording</Badge> : <Badge>idle</Badge>}
    >
      <div className="stack tight">
        <TextField
          label="Flag"
          value={flag}
          placeholder="e.g. step-response"
          optional
          onChange={setFlag}
          help="Marks this instant, and labels the start or stop of a record."
        />
        <div className="row">
          {recording ? (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void rig
                  .run("Stop recording", (transport) => transport.stopRecording(flag || undefined))
                  .then(() => setFlag(""))
              }
            >
              Stop recording
            </button>
          ) : (
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() =>
                void rig
                  .run("Start recording", (transport) => transport.startRecording(flag || undefined))
                  .then(() => setFlag(""))
              }
            >
              Start recording
            </button>
          )}
          <button
            type="button"
            disabled={busy || !flag.trim()}
            onClick={() =>
              void rig.run("Add flag", (transport) => transport.addFlag(flag)).then(() => setFlag(""))
            }
          >
            Add flag
          </button>
        </div>
      </div>
    </Card>
  );
}
