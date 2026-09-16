import { memo, useState } from "react";
import { Button, Chip, LinearProgress, Stack, Typography } from "@mui/material";
import StopIcon from "@mui/icons-material/Stop";
import { useRig } from "@flyball/react";
import { humanise } from "@flyball/client";
import { stepOf } from "../model.js";
import { hashFor } from "../router.js";
import { useEventsData, useRigData } from "../dashboard/context.js";
import { humaniseSubject } from "./Events.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const ProgramWidget = memo(function ProgramWidget({ config }: WidgetComponentProps) {
  const rig = useRig();
  const { programmer } = useRigData();
  const events = useEventsData();
  const [busy, setBusy] = useState(false);
  const p = programmer.data;
  const running = p?.running ?? false;
  const shown = Math.max(0, Number(config.events ?? 5));
  const recent = events.filter((e) => e.scope === "program").slice(-shown).reverse();
  // The programmer says step and command; the program's name is in the step events' subject (`anneal[4]`).
  const latest = [...events].reverse().find((e) => e.scope === "program" && e.kind === "step");
  const name = latest ? latest.subject.replace(/\[\d+\]$/, "") : null;
  const interrupt = () => {
    setBusy(true);
    rig
      .interruptProgram()
      .catch(() => undefined)
      .finally(() => {
        setBusy(false);
        programmer.refresh();
      });
  };
  return (
    <Stack spacing={1} sx={{ minHeight: 0, flex: "1 1 auto" }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip label={p ? (running ? "running" : "idle") : "…"} color={running ? "success" : "default"} />
        {running && name && (
          <Typography fontWeight={600} component="a" href={hashFor("programs", name)} sx={{ color: "inherit", textDecoration: "none", "&:hover": { textDecoration: "underline" } }}>
            {name}
          </Typography>
        )}
        <Typography variant="body2" color="text.secondary">
          {!p ? "…" : running ? `step ${stepOf(p)}${p.command ? ` · ${humanise(p.command)}` : ""}` : "nothing is running"}
        </Typography>
        {running && config.interrupt !== false && (
          <Button variant="outlined" color="error" startIcon={<StopIcon />} onClick={interrupt} disabled={busy} sx={{ ml: "auto" }}>
            Interrupt
          </Button>
        )}
      </Stack>
      {running && p && p.steps > 0 && <LinearProgress variant="determinate" value={(100 * Math.min(p.step + 1, p.steps)) / p.steps} />}
      {shown > 0 && (
        <Stack spacing={0.25} sx={{ overflow: "auto", minHeight: 0 }}>
          {recent.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              no program events yet
            </Typography>
          )}
          {recent.map((e, i) => (
            <Stack key={`${e.time_ns}-${i}`} direction="row" spacing={1} alignItems="baseline" sx={{ fontSize: "0.85rem" }}>
              <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                {new Date(e.time_ns / 1e6).toLocaleTimeString()}
              </Typography>
              <Typography variant="body2" color={e.level === "ERROR" ? "error" : e.level === "WARNING" ? "warning.main" : "text.secondary"} sx={{ whiteSpace: "nowrap" }}>
                {humanise(e.kind)}
              </Typography>
              <Typography variant="body2" noWrap title={`${humaniseSubject(e.subject)}: ${e.message}`}>
                {e.message}
              </Typography>
            </Stack>
          ))}
        </Stack>
      )}
    </Stack>
  );
});

export const program: WidgetKind = {
  kind: "program",
  label: "Program",
  description: "What the programmer is running: the program, its step, progress, and the last few program events.",
  category: "control",
  defaultSize: { w: 4, h: 5 },
  minSize: { w: 3, h: 2 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      events: { type: "integer", title: "Recent events", default: 5, minimum: 0, maximum: 50, description: "How many program events to list under the status; 0 for none." },
      interrupt: { type: "boolean", title: "Interrupt button", default: true },
    },
  }),
  defaultConfig: () => ({ events: 5, interrupt: true }),
  titleFor: () => "Program",
  Component: ProgramWidget,
};
