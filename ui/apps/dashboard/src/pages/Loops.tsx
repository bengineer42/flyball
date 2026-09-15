import { Alert, Typography } from "@mui/material";
import { LoopPanel, useLoops, useRigSchema } from "@flyball/react";
import { WindowSelect } from "../WindowSelect.js";

export interface LoopsProps {
  windowS: number;
  onWindow(s: number): void;
  /** One loop only (`#/loops/{name}`). */
  name?: string | null;
}

/** One `LoopPanel` per loop, live from `/ws/loops`; a loop is named after the actuator it drives. */
export function Loops({ windowS, onWindow, name = null }: LoopsProps) {
  const schema = useRigSchema();
  const { loops, history, status } = useLoops(3600);
  const shown = name === null ? Object.values(loops) : loops[name] ? [loops[name]] : [];
  if (name !== null && shown.length === 0)
    return status === "connecting" ? <Typography color="text.secondary">loading…</Typography> : <Alert severity="warning">No loop named {name}.</Alert>;
  if (shown.length === 0)
    return (
      <Typography color="text.secondary">
        {status === "connecting" ? "loading…" : "This rig has no loops attached."}
      </Typography>
    );
  return (
    <div className="tiles tiles-full">
      {shown.map((l) => (
        <LoopPanel
          key={l.name}
          loop={l}
          history={history[l.name]}
          demandUnit={schema.data?.actuators[l.name]?.demand_unit}
          windowS={windowS}
          controls={<WindowSelect value={windowS} onChange={onWindow} />}
        />
      ))}
    </div>
  );
}
