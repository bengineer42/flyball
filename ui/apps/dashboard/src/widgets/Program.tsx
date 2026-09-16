import { memo, useState } from "react";
import { Button, Chip, LinearProgress, Typography } from "@mui/material";
import StopIcon from "@mui/icons-material/Stop";
import { useRig } from "@flyball/react";
import { humanise } from "@flyball/client";
import { stepOf } from "../model.js";
import { hashFor } from "../router.js";
import { useEventsData, useRigData } from "../dashboard/context.js";
import { humaniseSubject, TIME } from "./Events.js";
import { rowsThatFit } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/** The status row (28px), the progress bar (4px) and the two gaps between them and the list (dashboard.css `.dash-program`). */
const HEAD_PX = 28 + 8 + 4 + 8;
/** One event line: 20px line-height, no padding. */
const ROW_PX = 20;

/**
 * State chip · program · step, an Interrupt button while running, the
 * progress bar, then as many recent program events as the body holds (never
 * a scrollbar). Body only; the frame is `WidgetFrame`'s.
 */
const ProgramWidget = memo(function ProgramWidget({ config, widget }: WidgetComponentProps) {
  const rig = useRig();
  const { programmer, rowHeight } = useRigData();
  const events = useEventsData();
  const [busy, setBusy] = useState(false);
  const p = programmer.data;
  const running = p?.running ?? false;
  // Stays set (with the error) until the next run clears it, even once `running` goes false.
  const failed = p?.failed ?? false;
  const shown = Math.min(Math.max(0, Number(config.events ?? 5)), rowsThatFit(widget.h, rowHeight, true, HEAD_PX, ROW_PX));
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
    <div className="dash-program">
      <div className="dash-program-status">
        <Chip size="small" label={p ? (running ? "running" : failed ? "failed" : "idle") : "…"} color={running ? "success" : failed ? "error" : "default"} />
        {running && name && (
          <Typography fontWeight={600} noWrap component="a" href={hashFor("programs", name)} sx={{ color: "inherit", textDecoration: "none", "&:hover": { textDecoration: "underline" } }}>
            {name}
          </Typography>
        )}
        <Typography variant="body2" noWrap color={failed ? "error" : "text.secondary"} sx={{ minWidth: 0 }}>
          {!p ? "…" : running ? `step ${stepOf(p)}${p.command ? ` · ${humanise(p.command)}` : ""}` : failed ? `failed${p.error ? ` · ${p.error}` : ""}` : "nothing is running"}
        </Typography>
        {running && config.interrupt !== false && (
          <Button size="small" variant="outlined" color="error" startIcon={<StopIcon />} onClick={interrupt} disabled={busy} sx={{ ml: "auto", flex: "none" }}>
            Interrupt
          </Button>
        )}
      </div>
      <LinearProgress className="dash-program-progress" variant="determinate" value={running && p && p.steps > 0 ? (100 * Math.min(p.step + 1, p.steps)) / p.steps : 0} sx={{ visibility: running ? "visible" : "hidden" }} />
      {shown > 0 && (
        <div className="dash-program-events">
          {recent.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              no program events yet
            </Typography>
          )}
          {recent.map((e, i) => (
            <div key={`${e.time_ns}-${i}`} className="dash-program-event" title={`${humaniseSubject(e.subject)}: ${e.message}`}>
              <span className="dash-program-time fb-muted">{TIME.format(new Date(e.time_ns / 1e6))}</span>
              <Typography variant="body2" component="span" color={e.level === "ERROR" ? "error" : e.level === "WARNING" ? "warning.main" : "text.secondary"} noWrap>
                {humanise(e.kind)}
              </Typography>
              <Typography variant="body2" component="span" noWrap>
                {e.message}
              </Typography>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

export const program: WidgetKind = {
  kind: "program",
  label: "Program",
  description: "What the programmer is running: the program, its step, progress, and the last few program events.",
  category: "control",
  // 8×6: a 150px body holds the status row, the bar and five 20px event lines; 6×3 the status row and one line (DESIGN-SPEC.md §10).
  defaultSize: { w: 8, h: 6 },
  minSize: { w: 6, h: 3 },
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
