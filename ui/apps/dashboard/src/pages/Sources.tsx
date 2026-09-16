import { useState } from "react";
import { Alert, Box, Link, Paper, Stack, Typography } from "@mui/material";
import { Gauge, Readout, SourcePanel, TimeSeries, UnitCharts, channelKey, useLatest, useQuery, useRig, useTraceRef } from "@flyball/react";
import type { ChannelOut, SourceOut } from "@flyball/client";
import { useRecordingExports } from "../model.js";
import { StateBlock } from "../cards.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { hashFor, hrefFor } from "../router.js";
import { channelIcon } from "../icons.js";
import { PageBar } from "../PageBar.js";
import { GroupingSelect, readGrouping, writeGrouping, type Grouping } from "../grouping.js";

export interface SourcesProps extends ChartSettings {
  sources: SourceOut[];
}

/** A channel's newest value as text; re-renders this element alone, at most four times a second. */
function LatestValue({ channel }: { channel: ChannelOut }) {
  const point = useLatest(channelKey(channel));
  return <>{point === undefined ? "—" : `${point.v.toFixed(channel.precision ?? 2)} ${channel.unit}`}</>;
}

/** Every source's charts: a panel per source, a chart per channel, or every channel of a unit on one chart. Every chart draws from the store: the page never re-renders on a sample. */
export function Sources({ sources, ...charts }: SourcesProps) {
  const { windowS, yScale, every } = charts;
  const channels = sources.flatMap((s) => s.channels);
  const live = useTraceRef(channels);
  const stored = useRecordingExports();
  const [grouping, setGrouping] = useState<Grouping>(readGrouping);
  const group = (g: Grouping) => {
    setGrouping(g);
    writeGrouping(g);
  };
  const bar = (
    <PageBar end={<ChartControls {...charts} unit={sources[0]?.channels[0]?.unit} />}>
      <GroupingSelect value={grouping} onChange={group} />
    </PageBar>
  );
  if (sources.length === 0)
    return (
      <>
        {bar}
        <StateBlock state="empty" message="No sources declared. Add a reader to the rig file to see channels here." />
      </>
    );
  const labelOf = (name: string) => sources.find((s) => s.name === name)?.label ?? name;
  return (
    <>
      {bar}
      {grouping === "unit" && (
        <div className="fb-charts">
          <UnitCharts channels={channels} source={live} sources={sources} height="auto" windowS={windowS} yScale={yScale} every={every} exportHref={stored.channels} />
        </div>
      )}
      {grouping === "channel" && (
        <div className="grid">
          {channels.map((c) => {
            const Icon = channelIcon(c);
            return (
              <Paper key={channelKey(c)} className="c6 xl4" sx={{ p: 3 }}>
                <Stack direction="row" alignItems="center" spacing={1} className="source-head">
                  <Icon fontSize="small" sx={{ color: "text.disabled" }} />
                  <Typography fontWeight={600}>
                    <Link href={hrefFor({ kind: "channel", name: c.source, measurand: c.measurand })} underline="hover" color="inherit">
                      {c.label}
                    </Link>
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    <Link href={hrefFor({ kind: "source", name: c.source })} underline="hover" color="inherit" title={c.source}>
                      {labelOf(c.source)}
                    </Link>
                  </Typography>
                  <Typography sx={{ ml: "auto !important", fontVariantNumeric: "tabular-nums" }}>
                    <LatestValue channel={c} />
                  </Typography>
                </Stack>
                <TimeSeries channel={c} source={live} height="auto" windowS={windowS} yScale={yScale} every={every} exportHref={stored.series(c)} />
              </Paper>
            );
          })}
        </div>
      )}
      {grouping === "source" && (
        <div className="grid">
          {sources.map((s) => (
            <div key={s.name} className="c6 xl4">
              <SourcePanel source={s} live={live} windowS={windowS} yScale={yScale} every={every} exportHref={stored.series} />
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/** Where a detail page sits: "sources › dry". */
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

/** One source: its panel full width, and a readout tile per channel. */
export function SourceDetail({ source, ...charts }: { source: SourceOut | undefined } & ChartSettings) {
  const { windowS, yScale, every } = charts;
  const stored = useRecordingExports();
  const live = useTraceRef(source?.channels);
  if (!source) return <Alert severity="warning">No such source.</Alert>;
  return (
    <>
      <PageBar end={<ChartControls {...charts} unit={source.channels[0]?.unit} />}>
        <Crumbs items={[{ label: "sources", href: hashFor("sources") }, { label: source.label ?? source.name }]} />
      </PageBar>
      <div className="grid">
        {source.channels.map((c) => {
          const Icon = channelIcon(c);
          return (
            <div key={channelKey(c)} className="tile-with-icon c3">
              <Icon fontSize="small" className="tile-icon" />
              <Readout channel={c} source={live} showSource={false} windowS={windowS} exportHref={stored.series(c)} />
            </div>
          );
        })}
        <div className="c12">
          <SourcePanel source={source} live={live} height="auto" windowS={windowS} yScale={yScale} every={every} exportHref={stored.series} />
        </div>
      </div>
    </>
  );
}

/** One channel: gauge, readout, its full-width trace, and the loops that regulate it. */
export function ChannelDetail({ source, measurand, ...charts }: { source: SourceOut | undefined; measurand: string } & ChartSettings) {
  const { windowS, yScale, every } = charts;
  const rig = useRig();
  const stored = useRecordingExports();
  const loops = useQuery(() => rig.loops(), [rig], { refreshMs: 10000 });
  const channel = source?.channels.find((c) => c.measurand === measurand);
  const live = useTraceRef(channel ? [channel] : undefined);
  // The gauge is this page's only prop-fed live element: the page re-renders on its channel alone, at most four times a second.
  const last = useLatest(channel ? channelKey(channel) : undefined)?.v;
  if (!source || !channel) return <Alert severity="warning">No such channel.</Alert>;
  const users = loops.data?.filter((l) => l.channel.source === source.name && l.channel.measurand === measurand) ?? [];
  return (
    <>
      <PageBar end={<ChartControls {...charts} unit={channel.unit} />}>
        <Crumbs items={[{ label: "sources", href: hashFor("sources") }, { label: source.label ?? source.name, href: hashFor("sources", source.name) }, { label: channel.label }]} />
      </PageBar>
      <Stack direction={{ xs: "column", sm: "row" }} spacing="16px" alignItems="stretch" sx={{ mb: "16px" }}>
        <Paper sx={{ p: 3, display: "flex", alignItems: "center", justifyContent: "center", minWidth: 200 }}>
          <Gauge channel={channel} value={last} height={180} />
        </Paper>
        <Box sx={{ flexGrow: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "16px" }}>
          <Readout channel={channel} source={live} windowS={windowS} sparkline={false} exportHref={stored.series(channel)} />
          <Paper sx={{ p: 3, flexGrow: 1 }}>
            <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.75 }}>
              Loops
            </Typography>
            {users.length > 0 ? (
              users.map((l) => (
                <Typography key={l.name}>
                  <Link href={hrefFor({ kind: "loop", name: l.name })} underline="hover">
                    {l.label ?? l.name}
                  </Link>{" "}
                  <Typography component="span" variant="body2" color="text.secondary">
                    {l.label && `${l.name} · `}
                    {l.mode}
                    {l.reference !== null && ` · reference ${l.reference} ${channel.unit}`}
                  </Typography>
                </Typography>
              ))
            ) : (
              <Typography variant="body2" color="text.secondary">
                {loops.data ? "no loop regulates this channel" : "…"}
              </Typography>
            )}
          </Paper>
        </Box>
      </Stack>
      <Paper sx={{ p: 3 }}>
        <Stack direction="row" alignItems="center" spacing={1} className="section-head" flexWrap="wrap" useFlexGap>
          <Typography fontWeight={600}>{channel.label}</Typography>
          <Typography variant="body2" color="text.secondary">
            {source.name}.{measurand} · {channel.unit}
            {channel.range && ` · range ${channel.range[0]} – ${channel.range[1]}`}
          </Typography>
        </Stack>
        <TimeSeries channel={channel} source={live} height={320} windowS={windowS} yScale={yScale} every={every} exportHref={stored.series(channel)} />
      </Paper>
    </>
  );
}
