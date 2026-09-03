/**
 * What the controller is holding.
 *
 * `demand` is the law's output before the split-range mapping clamps it, so it
 * can sit outside 0-100 when the rig is railed. That is worth seeing: a demand
 * pinned past the wet line means the target is unreachable with these sources,
 * not that the controller is broken.
 */

import type { ControllerState } from "../api/types";
import { FLOW_SCALE_LABELS } from "../domain/flows";
import { formatPercent } from "../domain/units";
import { describeLaw } from "../programs/steps";
import { Badge, Card, Tile } from "./primitives";

export interface ControllerStatusProps {
  controller: ControllerState | null | undefined;
  processHumidity: number | null;
}

export function ControllerStatus({ controller, processHumidity }: ControllerStatusProps) {
  if (!controller) {
    return (
      <Card title="Controller">
        <Tile
          label="Set point"
          value={null}
          absentNote="No controller running. Start one from Control, or run a program."
        />
      </Card>
    );
  }

  const error =
    processHumidity === null ? null : processHumidity - controller.set_point;
  const railed = controller.demand < 0 || controller.demand > 100;

  return (
    <Card
      title="Controller"
      hint={describeLaw(controller.law ?? controller.type)}
      actions={
        controller.suspended ? (
          <Badge tone="warning">suspended — pumps under manual control</Badge>
        ) : (
          <Badge tone="good" dot>
            regulating
          </Badge>
        )
      }
    >
      <div className="grid cols-4">
        <Tile
          label="Set point"
          colour="var(--series-setpoint)"
          value={formatPercent(controller.set_point)}
        />
        <Tile
          label="Error"
          value={error === null ? null : `${error > 0 ? "+" : ""}${error.toFixed(2)}%`}
          absentNote="No process reading to compare against."
          sub="reading − set point"
          small
        />
        <Tile
          label="Demand"
          value={formatPercent(controller.demand)}
          sub={railed ? "past the source lines — railed" : "law output"}
          small
        />
        <Tile
          label="Flow"
          value={`${controller.flow.scale === "absolute" ? controller.flow.value.toFixed(2) : `${(controller.flow.value * 100).toFixed(0)}%`}`}
          sub={FLOW_SCALE_LABELS[controller.flow.scale]}
          small
        />
      </div>
    </Card>
  );
}
