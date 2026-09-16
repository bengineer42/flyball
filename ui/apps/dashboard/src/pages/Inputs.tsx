import { useMemo, useState } from "react";
import { Alert, Box, Link, Paper, Stack, Typography } from "@mui/material";
import { DeviceSignals, Gauge, Readout, TimeSeries, UnitCharts, WritePanel, useControllers, useLatestValue, useSignal, useTraceRef } from "@flyball/react";
import { describeController, describeSignal, deviceOf, formatValue, publishes, signalsOf, writable, type DeviceOut, type SignalOut } from "@flyball/client";
import { useRecordingExports } from "../model.js";
import { StateBlock } from "../cards.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { hashFor, hrefFor } from "../router.js";
import { signalIcon } from "../icons.js";
import { PageBar } from "../PageBar.js";
import { GroupingSelect, readGrouping, writeGrouping, type Grouping } from "../grouping.js";
import { isNumeric, useValueReadout } from "../valueReadout.js";

export interface InputsProps extends ChartSettings {
  devices: DeviceOut[];
}

/** The devices that publish anything, each with its publishing signals flattened: what the Inputs page shows. */
export function publishingOf(devices: DeviceOut[]): Array<{ device: DeviceOut; signals: SignalOut[] }> {
  return devices.map((device) => ({ device, signals: signalsOf(device.signals).filter(publishes) })).filter((d) => d.signals.length > 0);
}

/** A device's display name by name, for headings and hints. */
export const deviceLabel = (devices: ReadonlyArray<Pick<DeviceOut, "name" | "label">>, name: string) => devices.find((d) => d.name === name)?.label ?? name;

/** A signal's newest value as text, by dtype; re-renders this element alone, at most four times a second. */
function LatestValue({ signal }: { signal: SignalOut }) {
  const point = useLatestValue(signal.address);
  if (point === undefined) return <>—</>;
  if (signal.dtype === "bool") return <>{point.value ? "on" : "off"}</>;
  return <>{formatValue(point.value, { unit: signal.unit, precision: signal.precision ?? undefined })}</>;
}

/**
 * Every publishing signal: a tree per device (namespaces as groups, a readout
 * per signal), a chart per signal, or every signal of a unit on one chart.
 * Every chart draws from the store: the page never re-renders on a sample.
 */
export function Inputs({ devices, ...charts }: InputsProps) {
  const { windowS, yScale, every } = charts;
  const publishing = useMemo(() => publishingOf(devices), [devices]);
  const signals = useMemo(() => publishing.flatMap((d) => d.signals), [publishing]);
  // A non-number never reaches a chart axis: the unit and per-signal charts are numeric signals only.
  const numericSignals = useMemo(() => signals.filter(isNumeric), [signals]);
  const live = useTraceRef(useMemo(() => signals.map((s) => s.address), [signals]));
  const stored = useRecordingExports();
  const [grouping, setGrouping] = useState<Grouping>(readGrouping);
  const group = (g: Grouping) => {
    setGrouping(g);
    writeGrouping(g);
  };
  const bar = (
    <PageBar end={<ChartControls {...charts} unit={numericSignals[0]?.unit} />}>
      <GroupingSelect value={grouping} onChange={group} />
    </PageBar>
  );
  const shown = devices.filter((d) => d.kind !== "simulation");
  if (shown.length === 0)
    return (
      <>
        {bar}
        <StateBlock state="empty" message="No devices declared. Add one to the rig file to see its readings here." />
      </>
    );
  return (
    <>
      {bar}
      {grouping === "unit" && (
        <div className="fb-charts">
          <UnitCharts signals={numericSignals} source={live} devices={devices} height="auto" windowS={windowS} yScale={yScale} every={every} exportHref={stored.signals} />
        </div>
      )}
      {grouping === "signal" && (
        <div className="grid">
          {signals.map((s) => {
            const Icon = signalIcon(s);
            const device = deviceOf(s.address);
            return (
              <Paper key={s.address} className="c6 xl4" sx={{ p: 3 }}>
                <Stack direction="row" alignItems="center" spacing={1} className="source-head">
                  <Icon fontSize="small" sx={{ color: "text.disabled" }} />
                  <Typography fontWeight={600}>
                    <Link href={hrefFor({ kind: "signal", name: s.address })} underline="hover" color="inherit" title={s.address}>
                      {describeSignal(s)}
                    </Link>
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    <Link href={hrefFor({ kind: "device", name: device })} underline="hover" color="inherit" title={device}>
                      {deviceLabel(devices, device)}
                    </Link>
                  </Typography>
                  <Typography sx={{ ml: "auto !important", fontVariantNumeric: "tabular-nums" }}>
                    <LatestValue signal={s} />
                  </Typography>
                </Stack>
                {/* A non-number never reaches this chart: it has its value in the header alone. */}
                {isNumeric(s) && <TimeSeries signal={s} source={live} height="auto" windowS={windowS} yScale={yScale} every={every} exportHref={stored.series(s.address)} />}
              </Paper>
            );
          })}
        </div>
      )}
      {grouping === "device" && (
        <div className="grid">
          {shown.map((device) => (
            <div key={device.name} className="c6 xl4">
              <DeviceSignals device={device} windowS={windowS} every={every} exportHref={(s) => stored.series(s.address)} />
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/** Where a detail page sits: "inputs › furnace › zone1". */
export function Crumbs({ items }: { items: Array<{ label: string; href?: string }> }) {
  return (
    <Stack direction="row" spacing={0.75} alignItems="baseline">
      {items.map((it, i) => (
        <Stack key={i} direction="row" spacing={0.75} alignItems="baseline">
          {i > 0 && (
            <Typography variant="body2" color="text.disabled">
              ›
            </Typography>
          )}
          {it.href ? (
            <Link href={it.href} underline="hover" variant="body2">
              {it.label}
            </Link>
          ) : (
            <Typography variant="body2" fontWeight={600}>
              {it.label}
            </Typography>
          )}
        </Stack>
      ))}
    </Stack>
  );
}

/** The signal at `address` on one of `devices`, or undefined. */
export function signalAt(devices: DeviceOut[], address: string): SignalOut | undefined {
  const device = devices.find((d) => d.name === deviceOf(address));
  return device ? signalsOf(device.signals).find((s) => s.address === address) : undefined;
}

/**
 * One signal: for a publishing one its gauge, readout and full-width trace;
 * for a writable one its write panel; either way the controllers that
 * regulate it (as their source) or drive it (as their target).
 */
export function SignalDetail({ devices, address, ...charts }: { devices: DeviceOut[]; address: string } & ChartSettings) {
  const { windowS, yScale, every } = charts;
  const stored = useRecordingExports();
  const signal = signalAt(devices, address);
  const streams = signal !== undefined && publishes(signal);
  const numeric = signal !== undefined && isNumeric(signal);
  // A non-number never reaches the gauge, the sparkline readout or the trace below: only a numeric signal charts.
  const chartable = streams && numeric;
  const live = useTraceRef(useMemo(() => (chartable ? [address] : []), [chartable, address]));
  const { controllers } = useControllers();
  // The gauge is this page's only prop-fed live element: the page re-renders on its signal alone, at most four times a second.
  const last = useSignal(chartable ? address : undefined)?.v;
  const value = useValueReadout(streams && !numeric ? signal : undefined);
  if (!signal) return <Alert severity="warning">No signal at {address}.</Alert>;
  const device = deviceOf(address);
  const regulating = Object.values(controllers).filter((c) => c.source === address);
  const driving = Object.values(controllers).find((c) => c.target === address);
  const users = driving ? [driving, ...regulating] : regulating;
  return (
    <>
      <PageBar end={<ChartControls {...charts} unit={signal.unit} />}>
        <Crumbs items={[{ label: "inputs", href: hashFor("inputs") }, { label: deviceLabel(devices, device), href: hrefFor({ kind: "device", name: device }) }, { label: describeSignal(signal) }]} />
      </PageBar>
      <Stack direction={{ xs: "column", sm: "row" }} spacing="16px" alignItems="stretch" sx={{ mb: "16px" }}>
        {chartable && (
          <Paper sx={{ p: 3, display: "flex", alignItems: "center", justifyContent: "center", minWidth: 200 }}>
            <Gauge signal={signal} value={last} height={180} />
          </Paper>
        )}
        <Box sx={{ flexGrow: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "16px" }}>
          {chartable && <Readout signal={signal} source={live} windowS={windowS} sparkline={false} exportHref={stored.series(address)} />}
          {streams && !numeric && (
            <Paper sx={{ p: 3 }}>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 0.75 }}>
                {describeSignal(signal)}
              </Typography>
              <div className="fb-readout-value">{value.body}</div>
            </Paper>
          )}
          {writable(signal) && <WritePanel signal={signal} />}
          <Paper sx={{ p: 3, flexGrow: 1 }}>
            <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.75 }}>
              Controllers
            </Typography>
            {users.length > 0 ? (
              users.map((c) => (
                <Typography key={c.name}>
                  <Link href={hrefFor({ kind: "controller", name: c.name })} underline="hover">
                    {describeController(c)}
                  </Link>{" "}
                  <Typography component="span" variant="body2" color="text.secondary">
                    {c.label && `${c.name} · `}
                    {c.target === address ? "drives this signal" : "regulates this signal"} · {c.mode}
                    {c.setpoint !== null && c.setpoint !== undefined && ` · target ${c.setpoint}`}
                  </Typography>
                </Typography>
              ))
            ) : (
              <Typography variant="body2" color="text.secondary">
                no controller regulates or drives this signal
              </Typography>
            )}
          </Paper>
        </Box>
      </Stack>
      {chartable && (
        <Paper sx={{ p: 3 }}>
          <Stack direction="row" alignItems="center" spacing={1} className="section-head" flexWrap="wrap" useFlexGap>
            <Typography fontWeight={600}>{describeSignal(signal)}</Typography>
            <Typography variant="body2" color="text.secondary">
              {address} [{signal.access.toUpperCase()}] · {signal.unit}
              {signal.range && ` · range ${signal.range[0]} – ${signal.range[1]}`}
            </Typography>
          </Stack>
          <TimeSeries signal={signal} source={live} height={320} windowS={windowS} yScale={yScale} every={every} exportHref={stored.series(address)} />
        </Paper>
      )}
    </>
  );
}
