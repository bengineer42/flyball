/**
 * Faults the loop reported.
 *
 * The warnings socket is lossy by design on the daemon side, so this is a view
 * of what arrived, not a log. Anything that must survive belongs in the record.
 */

import type { Warning } from "../api/types";
import { formatClock } from "../domain/units";
import { Card } from "./primitives";

export interface WarningsPanelProps {
  warnings: ReadonlyArray<Warning>;
  onClear(): void;
  limit?: number;
}

export function WarningsPanel({ warnings, onClear, limit = 8 }: WarningsPanelProps) {
  return (
    <Card
      title="Warnings"
      hint={warnings.length === 0 ? "none" : `${warnings.length} received`}
      actions={
        warnings.length > 0 && (
          <button type="button" className="ghost small" onClick={onClear}>
            Clear
          </button>
        )
      }
    >
      {warnings.length === 0 ? (
        <p className="hint" style={{ margin: 0 }}>
          Nothing reported since this page connected.
        </p>
      ) : (
        <div className="stack tight">
          {warnings.slice(0, limit).map((warning, index) => (
            <div key={`${warning.time}-${index}`} className="row" style={{ gap: 8 }}>
              <span className="hint mono">{formatClock(warning.time)}</span>
              <span>{warning.error}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
