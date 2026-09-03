/**
 * The rig at a glance.
 *
 * Three charts, not one with three scales: humidity, temperature and flow are
 * different measures and get different axes. Series that the rig cannot supply
 * are not drawn at all — an absent dry sensor is not a line at zero — and the
 * panels below say why.
 */

import { useMemo, useState } from "react";

import type { RigState } from "../api/types";
import { lineStatus } from "../domain/readings";
import { formatPercent, formatTemperature } from "../domain/units";
import { hasSeries, window as windowed, type Sample } from "../state/history";
import type { RigHandle } from "../state/useRig";
import { ControllerStatus } from "../components/ControllerStatus";
import { PumpsStatus } from "../components/PumpsStatus";
import { ReadingsPanel } from "../components/ReadingsPanel";
import { TimeSeriesChart, type SeriesSpec } from "../components/TimeSeriesChart";
import { WarningsPanel } from "../components/WarningsPanel";
import { Card, Note, Tile } from "../components/primitives";

const RANGES: ReadonlyArray<{ label: string; seconds: number }> = [
  { label: "5 min", seconds: 300 },
  { label: "15 min", seconds: 900 },
  { label: "1 hour", seconds: 3600 },
  { label: "All", seconds: Number.POSITIVE_INFINITY },
];

export interface DashboardProps {
  rig: RigHandle;
  state: RigState | null;
  defaultWindow: number;
}

export function Dashboard({ rig, state, defaultWindow }: DashboardProps) {
  const [range, setRange] = useState(defaultWindow);
  const data = useMemo(() => windowed(rig.history, range), [rig.history, range]);

  const process = lineStatus(state?.readings, "process");
  const spec = state?.pumps_spec ?? null;

  const humiditySeries = useMemo(() => {
    const series: SeriesSpec<Sample>[] = [
      {
        key: "process",
        label: "Process",
        colour: "var(--series-process)",
        value: (sample) => sample.process,
      },
    ];
    // Only offer a series the rig actually produces.
    if (hasSeries(rig.history, "setPoint")) {
      series.push({
        key: "setPoint",
        label: "Set point",
        colour: "var(--series-setpoint)",
        dashed: true,
        value: (sample) => sample.setPoint,
      });
    }
    if (hasSeries(rig.history, "expected")) {
      series.push({
        key: "expected",
        label: "Expected",
        colour: "var(--series-expected)",
        dashed: true,
        value: (sample) => sample.expected,
      });
    }
    return series;
  }, [rig.history]);

  const sourceSeries = useMemo(() => {
    const series: SeriesSpec<Sample>[] = [];
    if (hasSeries(rig.history, "wet")) {
      series.push({ key: "wet", label: "Wet line", colour: "var(--series-wet)", value: (s) => s.wet });
    }
    if (hasSeries(rig.history, "dry")) {
      series.push({ key: "dry", label: "Dry line", colour: "var(--series-dry)", value: (s) => s.dry });
    }
    return series;
  }, [rig.history]);

  const flowSeries: SeriesSpec<Sample>[] = [
    { key: "wetFlow", label: "Wet", colour: "var(--series-wet)", value: (sample) => sample.wetFlow },
    { key: "dryFlow", label: "Dry", colour: "var(--series-dry)", value: (sample) => sample.dryFlow },
  ];

  return (
    <div className="stack">
      <div className="grid cols-4">
        <Card>
          <Tile
            label="Process humidity"
            colour="var(--series-process)"
            value={process.kind === "reading" ? formatPercent(process.reading.humidity) : null}
            absentNote={
              process.kind === "failed" ? process.error : "No process reading from this rig yet."
            }
            sub={
              state?.controller
                ? `set point ${formatPercent(state.controller.set_point)}`
                : "no controller running"
            }
          />
        </Card>
        <Card>
          <Tile
            label="Temperature"
            value={process.kind === "reading" ? formatTemperature(process.reading.temperature) : null}
            absentNote="Comes with the process reading."
            sub="process line"
          />
        </Card>
        <Card>
          <Tile
            label="Expected humidity"
            colour="var(--series-expected)"
            value={
              state?.expected_humidity === null || state?.expected_humidity === undefined
                ? null
                : formatPercent(state.expected_humidity)
            }
            absentNote="Needs both source humidities and some flow."
            sub="from the current blend"
          />
        </Card>
        <Card>
          <Tile
            label="Total flow"
            value={
              state?.pumps
                ? `${(state.pumps.flows.wet + state.pumps.flows.dry).toFixed(2)}${spec?.units ? ` ${spec.units}` : ""}`
                : null
            }
            absentNote="No pumps attached."
            sub={
              state?.pumps
                ? `${((state.pumps.flows.wet / Math.max(state.pumps.flows.wet + state.pumps.flows.dry, 1e-9)) * 100).toFixed(0)}% wet`
                : undefined
            }
          />
        </Card>
      </div>

      <Card
        title="Humidity"
        hint="process against what the controller is asking for"
        actions={
          <div className="row">
            {RANGES.map((option) => (
              <button
                key={option.label}
                type="button"
                className={range === option.seconds ? "small" : "ghost small"}
                aria-pressed={range === option.seconds}
                onClick={() => setRange(option.seconds)}
              >
                {option.label}
              </button>
            ))}
          </div>
        }
      >
        <TimeSeriesChart
          data={data}
          x={(sample) => sample.time}
          series={humiditySeries}
          unit="%"
          height={260}
          emptyMessage="Waiting for the loop to publish. Two samples are needed before a line appears."
        />
      </Card>

      <div className="grid cols-2">
        <Card title="Source lines" hint="the wet and dry air being blended">
          {sourceSeries.length > 0 ? (
            <TimeSeriesChart
              data={data}
              x={(sample) => sample.time}
              series={sourceSeries}
              unit="%"
              height={180}
            />
          ) : (
            <Note>
              No sensors on the wet or dry lines, so there is nothing to plot. The controller uses
              the rig's configured source humidities instead, and the expected-humidity line above
              is derived from those.
            </Note>
          )}
        </Card>

        <Card title="Flow" hint="commanded, per line">
          <TimeSeriesChart
            data={data}
            x={(sample) => sample.time}
            series={flowSeries}
            unit={spec?.units ?? undefined}
            digits={2}
            height={180}
            emptyMessage="No pump output published yet."
          />
        </Card>
      </div>

      <ReadingsPanel readings={state?.readings} />

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

      <WarningsPanel warnings={rig.warnings} onClear={rig.clearWarnings} />
    </div>
  );
}
