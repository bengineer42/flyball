/**
 * The three sensor lines.
 *
 * Each line is rendered from its `LineStatus`, so the three cases stay visibly
 * distinct: a value, a fault in red, or an explained absence. The dry and wet
 * lines are usually absent — most rigs sense only the chamber — and that reads
 * as normal here rather than as something broken.
 */

import type { ReadingLine, ReadingsState } from "../api/types";
import { LINE_ABSENT_NOTES, LINE_LABELS, lineStatus } from "../domain/readings";
import { formatClock, formatPercent, formatTemperature } from "../domain/units";
import { Badge, Card, Tile } from "./primitives";

const COLOURS: Record<ReadingLine, string> = {
  process: "var(--series-process)",
  dry: "var(--series-dry)",
  wet: "var(--series-wet)",
};

export interface ReadingsPanelProps {
  readings: ReadingsState | undefined;
  /** Configured source humidities, used when a line has no sensor. */
  fallbacks?: Partial<Record<"dry" | "wet", number>>;
}

export function ReadingsPanel({ readings, fallbacks }: ReadingsPanelProps) {
  const lines: ReadingLine[] = ["process", "dry", "wet"];

  return (
    <Card title="Readings" hint="humidity and temperature per line">
      <div className="grid cols-3">
        {lines.map((line) => {
          const status = lineStatus(readings, line);
          const fallback = line === "process" ? undefined : fallbacks?.[line];

          if (status.kind === "failed") {
            return (
              <div key={line} className="stack tight">
                <Tile
                  label={
                    <>
                      {LINE_LABELS[line]} <Badge tone="critical">fault</Badge>
                    </>
                  }
                  value={null}
                  colour={COLOURS[line]}
                  absentNote={status.error}
                />
              </div>
            );
          }

          if (status.kind === "absent") {
            return (
              <Tile
                key={line}
                label={
                  <>
                    {LINE_LABELS[line]} <Badge>no sensor</Badge>
                  </>
                }
                value={null}
                colour={COLOURS[line]}
                absentNote={
                  fallback === undefined
                    ? LINE_ABSENT_NOTES[line]
                    : `${LINE_ABSENT_NOTES[line]} Assumed ${formatPercent(fallback)}.`
                }
              />
            );
          }

          return (
            <Tile
              key={line}
              label={LINE_LABELS[line]}
              value={formatPercent(status.reading.humidity)}
              colour={COLOURS[line]}
              sub={
                <>
                  {formatTemperature(status.reading.temperature)} ·{" "}
                  {status.reading.source ?? "unnamed"} · {formatClock(status.reading.time)}
                </>
              }
            />
          );
        })}
      </div>
    </Card>
  );
}
