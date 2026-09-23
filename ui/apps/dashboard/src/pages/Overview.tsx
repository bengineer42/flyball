import { memo, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Alert, Button, ButtonBase, Chip, Link, Paper, Stack, Typography } from "@mui/material";
import { PanelFrame, Readout, Ref, UnitCharts, groupByUnit, useHealth, countRender, useController, useControllers, useDeviceRuns, useEvents, useSignal, useTraceRef, type TraceRef, useStreamStatus } from "@flyball/react";
import { captionUnder, describeNamespace, deviceOf, isHousekeeping, signalTitleAt, deviceTitle, isNamespace, placeOf, publishes, setpointOf, signalsOf, titleFor, unitTitle, withUnit, type ControllerOut, type DeviceOut, type Place, type SignalOut } from "@flyball/client";
import { CircleIcon, OkIcon, SignalIcon, WarnIcon, signalIcon, PAGE_ICONS, type IconComponent } from "../icons.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { hashFor, hrefFor, type Page } from "../router.js";
import { DeviceSummaryCard, SectionHead, StateBlock, clickThrough, clickableSx } from "../cards.js";
import { useRecordingExports } from "../model.js";
import { PageBar } from "../PageBar.js";
import { GroupingSelect, readGrouping, writeGrouping, type Grouping } from "../grouping.js";
import { publishingOf } from "./Inputs.js";
import { isNumeric, useValueReadout } from "../valueReadout.js";

export interface OverviewProps extends ChartSettings {
  devices: DeviceOut[];
  onOpen(page: Page): void;
}

/* Normal is quiet: only warn and bad colour a tile (ISA-101), so an abnormal one is the one the eye lands on. */
type Tone = "ok" | "warn" | "bad";
const TONE_COLOR: Record<Tone, string | undefined> = { ok: undefined, warn: "warning.main", bad: "error.main" };

const uptime = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${Math.floor(s % 60)}s` : `${Math.floor(s)}s`;
};

/** A stat tile; with `href` the whole tile is a link (focusable, middle-clickable). One grid cell either way. */
function Stat({ icon: Icon, label, value, tone, href }: { icon: IconComponent; label: string; value: ReactNode; tone?: Tone; href?: string }) {
  const body = (
    <>
      <Icon fontSize="small" sx={{ color: (tone && TONE_COLOR[tone]) ?? "text.secondary" }} />
      <div>
        <Typography variant="overline" color="text.secondary" component="div" sx={{ lineHeight: 1.3 }}>
          {label}
        </Typography>
        <Typography fontWeight={600}>{value}</Typography>
      </div>
    </>
  );
  const sx = { display: "flex", alignItems: "center", gap: 3, px: 3, py: 2.25, height: "100%", boxSizing: "border-box", borderColor: tone ? TONE_COLOR[tone] : undefined } as const;
  if (!href) return <Paper className="c3" sx={sx}>{body}</Paper>;
  return (
    <ButtonBase component="a" href={href} className="c3" sx={{ display: "block", textAlign: "left", borderRadius: 1, "&.Mui-focusVisible": { outline: 2, outlineColor: "primary.main" } }}>
      <Paper sx={{ ...sx, ...clickableSx }}>{body}</Paper>
    </ButtonBase>
  );
}

/** A section's "→ somewhere" text action, for `SectionHead`'s `end` slot. */
function GoTo({ label, onClick }: { label: string; onClick(): void }) {
  return (
    <Button onClick={onClick} sx={{ py: 0 }}>
      {label} →
    </Button>
  );
}

/** A non-numeric signal's tile: same frame as `Readout`, its value a chip or a compact block by dtype -- never a gauge or a series. */
function ValueTile({ signal, place, showDevice }: { signal: SignalOut; place?: Place; showDevice: boolean }) {
  const { level, footer, body } = useValueReadout(signal);
  const title = signalTitleAt(signal, place ?? {});
  return (
    <PanelFrame
      className="fb-readout"
      severity={level}
      title={
        <Ref kind="signal" name={signal.address}>
          {title}
        </Ref>
      }
      subtitle={!showDevice ? undefined : place && captionUnder(title, signal, place) ? <Ref kind="device" name={place.device?.name ?? deviceOf(signal.address)}>{captionUnder(title, signal, place)}</Ref> : <Ref kind="device" name={deviceOf(signal.address)} />}
      // Always a footer line, blank while fresh, as `Readout` reserves: a stale line that comes and goes resizes the tile.
      footer={footer ?? "\u00a0"}
    >
      <div className="fb-readout-value">{body}</div>
    </PanelFrame>
  );
}

/**
 * A signal's readout tile, with its kind's icon in the corner. Reads the store itself (the
 * stale threshold from its device's run), so only the tile re-renders on its signal's samples;
 * memoised on primitives, so the page re-rendering does not touch forty tiles. A non-number
 * never reaches the gauge/series `Readout`: it gets `ValueTile`'s chip or block instead.
 */
const Tile = memo(function Tile({ signal, live, windowS, showDevice, place, exportHref, className }: { signal: SignalOut; live: TraceRef; windowS: number; showDevice: boolean; place?: Place; exportHref?: string; className?: string }) {
  const Icon = signalIcon(signal);
  return (
    <div className={className ?? "tile-with-icon c3"}>
      <Icon fontSize="small" className="tile-icon" />
      {isNumeric(signal) ? (
        <Readout signal={signal} source={live} showDevice={showDevice} place={place} windowS={windowS} exportHref={exportHref} />
      ) : (
        <ValueTile signal={signal} place={place} showDevice={showDevice} />
      )}
    </div>
  );
});

/** A box of tiles read as one sample: a namespace's publishing signals, or a device's root-level ones. */
interface SampleBox {
  /** The namespace's address, or the device's name for its root signals. */
  address: string;
  /** The heading; none for a device's root box when it is the device's only box (the device's own heading serves). */
  title?: string;
  signals: SignalOut[];
  /** Tile columns asked for: members stack singly up to three, then a column per three, at most four. */
  cols: number;
}

/**
 * A device's publishing signals as sample boxes, in tree order: one per
 * top-level namespace (a sample -- `hum_sensors.chamber` is humidity and
 * temperature read together; deeper namespaces fold into it), and one for
 * the signals at the device's root, first, when there are any.
 */
export function sampleBoxes(device: DeviceOut): SampleBox[] {
  const cols = (n: number) => Math.min(4, Math.ceil(n / 3));
  const boxes: SampleBox[] = [];
  const root = signalsOf(device.signals.filter((n) => !isNamespace(n))).filter((s) => publishes(s) && !isHousekeeping(s));
  for (const node of device.signals) {
    if (!isNamespace(node)) continue;
    const members = signalsOf(node.signals).filter((s) => publishes(s) && !isHousekeeping(s));
    if (members.length) boxes.push({ address: node.address, title: describeNamespace(node), signals: members, cols: cols(members.length) });
  }
  if (root.length) boxes.unshift({ address: device.name, title: boxes.length ? deviceTitle(device) : undefined, signals: root, cols: cols(root.length) });
  return boxes;
}

/**
 * When a device last published, from its first publishing signal's newest
 * point; re-renders this line alone. Says nothing while the stream is still
 * connecting: "no sample yet" beside a tile that already shows one reads as
 * a contradiction, and on first paint that is the usual case.
 */
function LastSample({ first }: { first: SignalOut | undefined }) {
  const point = useSignal(first?.address);
  const { streams } = useStreamStatus();
  const settled = streams.length > 0 && streams.every((s) => s !== "connecting");
  return <>{point ? `sample ${new Date(point.t * 1000).toLocaleTimeString()}` : settled ? "no sample yet" : ""}</>;
}

/** The devices' cards, subscribed to their runs (a last-read time that moves once a second) so the page above is not. */
function DeviceCards({ devices }: { devices: DeviceOut[] }) {
  const runs = useDeviceRuns();
  return (
    <div className="grid">
      {devices.map((d) => (
        <DeviceSummaryCard key={d.name} className="c3" device={d} run={runs[d.name]} />
      ))}
    </div>
  );
}

/** One controller's card: what it drives from what, its mode, and the live reading, setpoint and demand. */
const ControllerCard = memo(function ControllerCard({ name, title, sourceUnit, sourceTitle, targetTitle, precision }: { name: string; title: string; sourceUnit: string; sourceTitle: string; targetTitle: string; precision: number }) {
  const c = useController(name);
  if (!c) return null;
  const href = hrefFor({ kind: "controller", name });
  const num = (v: number | null | undefined, unit: string) => (v == null ? "—" : withUnit(v.toFixed(precision), unit));
  const setpoint = setpointOf(c);
  return (
    <Paper className="c3" sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1, minWidth: 0, ...clickableSx }} onClick={clickThrough(href)}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <PAGE_ICONS.controllers fontSize="small" sx={{ color: "text.disabled" }} />
        <Typography fontWeight={600} noWrap>
          <Link href={href} underline="hover" color="inherit" title={c.name}>
            {c.label || title}
          </Link>
        </Typography>
        <Chip label={c.mode} size="small" variant="outlined" color={c.mode === "regulating" ? "primary" : "default"} sx={{ ml: "auto !important" }} className={`fb-mode fb-mode-${c.mode}`} />
      </Stack>
      <Typography variant="body2" color="text.secondary" title={`${c.measured_signal} → ${c.output_signal}`}>
        {sourceTitle} → {targetTitle}
      </Typography>
      <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
        reading {num(typeof c.measured?.value === "number" ? c.measured.value : null, sourceUnit)} · target {num(setpoint, sourceUnit)} · demand {num(c.output, c.output_unit)}
      </Typography>
    </Paper>
  );
});

/** Everything the rig knows about itself on one page: health strip, inputs, controllers, devices. */
export function Overview({ devices, onOpen, ...charts }: OverviewProps) {
  countRender("Overview");
  const { windowS, yScale } = charts;
  const publishing = useMemo(() => publishingOf(devices), [devices]);
  const signals = useMemo(() => publishing.flatMap((d) => d.signals), [publishing]);
  // The trend charts at the foot of the section: a non-number never reaches a chart axis.
  const numericSignals = useMemo(() => signals.filter(isNumeric), [signals]);
  const byAddress = useMemo(() => new Map(signals.map((s) => [s.address, s])), [signals]);
  // Every signal of the rig by address (a controller's output need not publish), with where it sits, for titles.
  const everySignal = useMemo(() => new Map(devices.flatMap((d) => signalsOf(d.signals).map((s) => [s.address, s] as const))), [devices]);
  const titleOf = (address: string) => {
    const signal = everySignal.get(address);
    return signal ? signalTitleAt(signal, placeOf(address, devices)) : address;
  };
  // One handle on the store for every chart and tile on the page; nothing here re-renders on a sample.
  const live = useTraceRef(useMemo(() => signals.map((s) => s.address), [signals]));
  const { events } = useEvents(500);
  const stored = useRecordingExports();
  const [grouping, setGrouping] = useState<Grouping>(readGrouping);
  const group = (g: Grouping) => {
    setGrouping(g);
    writeGrouping(g);
  };
  const health = useHealth(5000);
  const h = health.data;
  const { controllers } = useControllers();
  const controllerList = useMemo(() => Object.values(controllers).sort((a: ControllerOut, b: ControllerOut) => a.name.localeCompare(b.name)), [controllers]);
  const problems = events.filter((e) => e.level === "ERROR" || e.level === "WARNING").length;
  const errors = events.filter((e) => e.level === "ERROR").length;
  const shownDevices = devices.filter((d) => d.kind !== "simulation");
  const polled = h ? Object.keys(h.devices).length : 0;
  const stopped = h ? Object.values(h.devices).filter((d) => !d.running).length : 0;
  const warnings = hashFor("events", null, { level: "WARNING" });

  // Alarm summary (research §6): `/api/health.alarms` folds the signals outside their warn/alarm
  // band with the device conditions at WARNING/ERROR, so this tile and the app-bar chip agree.
  const amber = h?.alarms.warn ?? 0;
  const red = h?.alarms.alarm ?? 0;
  const conditionsCount = amber + red;
  const conditionsTone: Tone | undefined = red > 0 || (h?.alarms.max_level ?? 0) >= 40 ? "bad" : conditionsCount > 0 ? "warn" : undefined;

  return (
    <>
      <PageBar end={<ChartControls {...charts} unit={numericSignals[0]?.unit} />}>
        <GroupingSelect value={grouping} onChange={group} />
      </PageBar>
      <div className="grid stats">
        <Stat icon={h?.ok ? OkIcon : WarnIcon} label="rig" value={h ? (h.ok ? "ok" : "fault") : "…"} tone={h ? (h.ok ? "ok" : "bad") : undefined} href={hashFor("events")} />
        <Stat icon={SignalIcon} label="recording" value={h ? (h.recording ? "on" : "off") : "…"} tone={h?.recording ? "ok" : undefined} href={hashFor("sessions")} />
        <Stat icon={PAGE_ICONS.devices} label="devices" value={h ? `${polled - stopped}/${polled} polling` : "…"} tone={stopped ? "warn" : undefined} href={hashFor("devices")} />
        <Stat icon={PAGE_ICONS.controllers} label="controllers" value={h ? Object.keys(h.controllers).length : "…"} href={hashFor("controllers")} />
        <Stat icon={WarnIcon} label="conditions" value={h ? conditionsCount : "…"} tone={h ? conditionsTone : undefined} href={warnings} />
        <Stat icon={CircleIcon} label="waits" value={h ? h.waits.length : "…"} href={hashFor("events")} />
        <Stat icon={PAGE_ICONS.events} label="events" value={`${problems} warn/error of ${events.length}`} tone={errors ? "bad" : problems ? "warn" : undefined} href={hashFor("events")} />
        <Stat icon={PAGE_ICONS.sessions} label="uptime" value={h ? uptime(h.uptime_s) : "…"} />
      </div>

      {h && h.conditions.length > 0 && (
        <Stack spacing={0.5} sx={{ mb: 3 }}>
          {h.conditions.map((c) => (
            <Alert key={`${c.device}-${c.kind}-${c.since_ns}`} severity={c.level >= 40 ? "error" : "warning"}>
              <Link href={hrefFor({ kind: "device", name: c.device })} color="inherit" underline="hover">
                <strong>{c.device}</strong>
              </Link>{" "}
              <strong>{c.kind}</strong> {c.message}
            </Alert>
          ))}
        </Stack>
      )}

      <section>
        <SectionHead icon={PAGE_ICONS.inputs} title="Inputs" count={signals.length} end={<GoTo label="charts" onClick={() => onOpen("inputs")} />} />
        {signals.length === 0 && <StateBlock state="empty" message="No signal publishes. Add a device with a publishing signal to the rig file to see readings here." action={{ label: "View inputs page", onClick: () => onOpen("inputs") }} />}
        {signals.length > 0 && (
          <div className="grid">
            {grouping === "signal" && signals.map((s) => <Tile key={s.address} signal={s} live={live} windowS={windowS} showDevice place={placeOf(s.address, devices)} exportHref={stored.series(s.address)} />)}
            {grouping === "unit" &&
              groupByUnit(signals).map(({ unit, signals: ss }) => (
                <div key={unit} className="c12 unit-group">
                  <Stack direction="row" alignItems="center" spacing={1} className="source-head">
                    <Typography fontWeight={600}>{unitTitle(unit, ss)}</Typography>
                    <Typography variant="body2" color="text.secondary">
                      {ss.length} signal{ss.length === 1 ? "" : "s"}
                    </Typography>
                  </Stack>
                  <div className="grid">
                    {ss.map((s) => <Tile key={s.address} signal={s} live={live} windowS={windowS} showDevice place={placeOf(s.address, devices)} exportHref={stored.series(s.address)} />)}
                  </div>
                </div>
              ))}
            {grouping === "device" && (
              <div className="c12 sample-groups">
                {publishing.map(({ device, signals: ss }) => {
                  const href = hrefFor({ kind: "device", name: device.name });
                  const boxes = sampleBoxes(device);
                  // A device's share of the row follows its tile columns, so its boxes sit beside the other devices' at one tile width.
                  const cols = Math.max(1, boxes.reduce((n, box) => n + box.cols, 0));
                  return (
                    <div key={device.name} className="source-group sample-group" style={{ "--cols": cols } as CSSProperties}>
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={1}
                        className="source-head"
                        sx={{ cursor: "pointer", borderRadius: 1, "&:hover .source-name": { textDecoration: "underline" } }}
                        onClick={clickThrough(href)}
                      >
                        <PAGE_ICONS.devices fontSize="inherit" sx={{ color: "text.disabled", alignSelf: "center" }} />
                        <Typography fontWeight={600}>
                          <Link href={href} underline="hover" color="inherit" className="source-name" title={device.name}>
                            {deviceTitle(device)}
                          </Link>
                        </Typography>
                        <Typography variant="body2" color="text.secondary">
                          <LastSample first={ss.find((s) => s.latest) ?? ss[0]} />
                        </Typography>
                      </Stack>
                      <div className="sample-boxes">
                        {boxes.map((box) => (
                          <div key={box.address} className="sample-box" style={{ "--cols": box.cols } as CSSProperties}>
                            {box.title && (
                              <Typography component="h3" className="sample-box-head" title={box.address}>
                                {box.title}
                              </Typography>
                            )}
                            <div className="sample-box-tiles">
                              {box.signals.map((s) => (
                                <Tile key={s.address} signal={s} live={live} windowS={windowS} showDevice={false} exportHref={stored.series(s.address)} className="tile-with-icon" />
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="c12 fb-charts">
              <UnitCharts signals={numericSignals} source={live} devices={devices} height="auto" windowS={windowS} yScale={yScale} exportHref={stored.signals} />
            </div>
          </div>
        )}
      </section>

      <section>
        <SectionHead icon={PAGE_ICONS.controllers} title="Controllers" count={controllerList.length} end={<GoTo label="faceplates" onClick={() => onOpen("controllers")} />} />
        {controllerList.length === 0 && <StateBlock state="empty" message="No controller attached. Bind a publishing signal to a writable one to regulate it." action={{ label: "View controllers page", onClick: () => onOpen("controllers") }} />}
        {controllerList.length > 0 && (
          <div className="grid">
            {controllerList.map((c) => {
              const source = byAddress.get(c.measured_signal);
              return <ControllerCard key={c.name} name={c.name} title={titleOf(c.name)} sourceUnit={source?.unit ?? ""} sourceTitle={titleOf(c.measured_signal)} targetTitle={titleOf(c.output_signal)} precision={source?.precision ?? 1} />;
            })}
          </div>
        )}
      </section>

      <section>
        <SectionHead icon={PAGE_ICONS.devices} title="Devices" count={shownDevices.length} end={<GoTo label="all devices" onClick={() => onOpen("devices")} />} />
        {shownDevices.length === 0 && <StateBlock state="empty" message="No devices declared. Add one to the rig file to see it here." />}
        {shownDevices.length > 0 && <DeviceCards devices={shownDevices} />}
      </section>
    </>
  );
}
