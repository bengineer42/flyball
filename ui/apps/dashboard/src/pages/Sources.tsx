import { useState } from "react";
import { Alert, Box, Link, Paper, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { Gauge, Readout, SourcePanel, TimeSeries, UnitCharts, channelKey, useQuery, useRig, type Traces } from "@flyball/react";
import type { SourceOut } from "@flyball/client";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { hashFor, hrefFor } from "../router.js";
import { channelIcon } from "../icons.js";

const VIEW_KEY = "flyball.sources.view";
type View = "source" | "unit";
const readView = (): View => {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "unit" ? "unit" : "source";
  } catch {
    return "source";
  }
};

export interface SourcesProps extends ChartSettings {
  sources: SourceOut[];
  traces: Traces;
}

/** Every source's charts, grouped by source (one panel each) or by unit (every channel of a unit on one chart). */
export function Sources({ sources, traces, ...charts }: SourcesProps) {
  const { windowS, yScale, every } = charts;
  const [view, setView] = useState<View>(readView);
  const choose = (v: View | null) => {
    if (!v) return;
    setView(v);
    try {
      window.localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* not persisted */
    }
  };
  const toggle = (
    <ToggleButtonGroup exclusive size="small" value={view} onChange={(_e, v: View | null) => choose(v)} aria-label="group charts">
      <ToggleButton value="source" sx={{ py: 0.25 }}>
        by source
      </ToggleButton>
      <ToggleButton value="unit" sx={{ py: 0.25 }}>
        by unit
      </ToggleButton>
    </ToggleButtonGroup>
  );
  if (sources.length === 0) return <Typography color="text.secondary">no sources declared</Typography>;
  if (view === "unit")
    return (
      <div className="tiles tiles-full">
        <UnitCharts
          channels={sources.flatMap((s) => s.channels)}
          traces={traces}
          windowS={windowS}
          yScale={yScale}
          every={every}
          controls={
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              {toggle}
              <ChartControls {...charts} unit={sources[0]?.channels[0]?.unit} />
            </Stack>
          }
        />
      </div>
    );
  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        {toggle}
        <Box sx={{ ml: "auto !important" }}>
          <ChartControls {...charts} unit={sources[0]?.channels[0]?.unit} />
        </Box>
      </Stack>
      <div className="tiles tiles-wide">
        {sources.map((s) => (
          <SourcePanel key={s.name} source={s} traces={traces} windowS={windowS} yScale={yScale} every={every} />
        ))}
      </div>
    </>
  );
}

/** Where a detail page sits: "sources › dry". */
export function Crumbs({ items }: { items: Array<{ label: string; href?: string }> }) {
  return (
    <Stack direction="row" spacing={0.75} alignItems="baseline" sx={{ mb: 1.5 }}>
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
export function SourceDetail({ source, traces, ...charts }: { source: SourceOut | undefined; traces: Traces } & ChartSettings) {
  const { windowS, yScale, every } = charts;
  if (!source) return <Alert severity="warning">No such source.</Alert>;
  return (
    <>
      <Crumbs items={[{ label: "sources", href: hashFor("sources") }, { label: source.name }]} />
      <div className="tiles" style={{ marginBottom: "0.9rem" }}>
        {source.channels.map((c) => {
          const trace = traces[channelKey(c)];
          const Icon = channelIcon(c);
          return (
            <div key={channelKey(c)} className="tile-with-icon">
              <Icon fontSize="small" className="tile-icon" />
              <Readout channel={c} t={trace?.t ?? []} v={trace?.v ?? []} showSource={false} windowS={windowS} />
            </div>
          );
        })}
      </div>
      <SourcePanel source={source} traces={traces} windowS={windowS} yScale={yScale} every={every} controls={<ChartControls {...charts} unit={source.channels[0]?.unit} />} />
    </>
  );
}

/** One channel: gauge, readout, its full-width trace, and the loops that regulate it. */
export function ChannelDetail({ source, measurand, traces, ...charts }: { source: SourceOut | undefined; measurand: string; traces: Traces } & ChartSettings) {
  const { windowS, yScale, every } = charts;
  const rig = useRig();
  const loops = useQuery(() => rig.loops(), [rig], { refreshMs: 10000 });
  const channel = source?.channels.find((c) => c.measurand === measurand);
  if (!source || !channel) return <Alert severity="warning">No such channel.</Alert>;
  const trace = traces[channelKey(channel)];
  const last = trace && trace.v.length ? trace.v[trace.v.length - 1] : undefined;
  const users = loops.data?.filter((l) => l.channel.source === source.name && l.channel.measurand === measurand) ?? [];
  return (
    <>
      <Crumbs items={[{ label: "sources", href: hashFor("sources") }, { label: source.name, href: hashFor("sources", source.name) }, { label: channel.label }]} />
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems="stretch" sx={{ mb: 1.5 }}>
        <Paper sx={{ p: 1.5, display: "flex", alignItems: "center", justifyContent: "center", minWidth: 200 }}>
          <Gauge channel={channel} value={last} height={180} />
        </Paper>
        <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", gap: 1.5 }}>
          <Readout channel={channel} t={trace?.t ?? []} v={trace?.v ?? []} windowS={windowS} sparkline={false} />
          <Paper sx={{ p: 1.5, flexGrow: 1 }}>
            <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.5 }}>
              Loops
            </Typography>
            {users.length > 0 ? (
              users.map((l) => (
                <Typography key={l.name}>
                  <Link href={hrefFor({ kind: "loop", name: l.name })} underline="hover">
                    {l.name}
                  </Link>{" "}
                  <Typography component="span" variant="body2" color="text.secondary">
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
      <Paper sx={{ p: 1.5 }}>
        <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
          <Typography fontWeight={600}>{channel.label}</Typography>
          <Typography variant="body2" color="text.secondary">
            {source.name}.{measurand} · {channel.unit}
            {channel.range && ` · range ${channel.range[0]} – ${channel.range[1]}`}
          </Typography>
          <Box sx={{ ml: "auto !important" }}>
            <ChartControls {...charts} unit={channel.unit} />
          </Box>
        </Stack>
        <TimeSeries channel={channel} t={trace?.t ?? []} v={trace?.v ?? []} height={280} windowS={windowS} yScale={yScale} every={every} />
      </Paper>
    </>
  );
}
