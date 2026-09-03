/**
 * What the pumps are doing.
 *
 * `flows` is commanded flow — effort multiplied by the configured maximum, the
 * way `DualPumps` derives it — not measurement. The panel says so, and keeps a
 * place for measured flow (R5) so the two can never be read as the same number.
 */

import type { PumpsSpec, PumpsState, RigState } from "../api/types";
import { totalFlow, wetFraction } from "../domain/flows";
import { formatPercent, formatQuantity } from "../domain/units";
import { Badge, Card, Tile } from "./primitives";

export interface PumpsStatusProps {
  pumps: PumpsState | null | undefined;
  spec: PumpsSpec | null | undefined;
  expectedHumidity: number | null | undefined;
  measured: RigState["measured_flows"];
}

export function PumpsStatus({ pumps, spec, expectedHumidity, measured }: PumpsStatusProps) {
  const units = spec?.units ?? null;

  if (!pumps) {
    return (
      <Card title="Pumps">
        <Tile
          label="Flows"
          value={null}
          absentNote="No pumps attached to this rig, or the loop has not published an output yet."
        />
      </Card>
    );
  }

  const total = totalFlow(pumps.flows);
  const wet = wetFraction(pumps.flows);

  return (
    <Card
      title="Pumps"
      hint="commanded flow (effort × max), not measured"
      actions={
        spec ? (
          <span className="hint mono">
            max {formatQuantity(spec.max_flows.wet, units)} wet / {formatQuantity(spec.max_flows.dry, units)} dry
          </span>
        ) : null
      }
    >
      <div className="grid cols-4">
        <Tile
          label="Wet flow"
          colour="var(--series-wet)"
          value={formatQuantity(pumps.flows.wet, units)}
          sub={`effort ${(pumps.efforts.wet * 100).toFixed(0)}%`}
        />
        <Tile
          label="Dry flow"
          colour="var(--series-dry)"
          value={formatQuantity(pumps.flows.dry, units)}
          sub={`effort ${(pumps.efforts.dry * 100).toFixed(0)}%`}
        />
        <Tile
          label="Total"
          value={formatQuantity(total, units)}
          sub={total > 0 ? `${(wet * 100).toFixed(0)}% wet` : "stopped"}
        />
        <Tile
          label="Expected humidity"
          colour="var(--series-expected)"
          value={expectedHumidity === null || expectedHumidity === undefined ? null : formatPercent(expectedHumidity)}
          absentNote={
            total > 0
              ? "Needs both source humidities before this blend implies a humidity."
              : "Nothing flowing, so the blend implies no humidity."
          }
          sub="what this blend should settle at"
        />
      </div>

      <div className="row" style={{ marginTop: 12, gap: 10 }}>
        <span className="hint">Measured flow</span>
        {measured ? (
          <span className="mono">
            wet {formatQuantity(measured.wet, units)} · dry {formatQuantity(measured.dry, units)}
          </span>
        ) : (
          <>
            <Badge>no flow sensors</Badge>
            <span className="hint">
              This rig reports no in-line flow measurement (R5); the figures above are commanded.
            </span>
          </>
        )}
      </div>
    </Card>
  );
}
