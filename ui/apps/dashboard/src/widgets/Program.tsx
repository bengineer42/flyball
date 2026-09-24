import { memo, useState } from "react";
import { Button, Chip, LinearProgress, Typography } from "@mui/material";
import CancelIcon from "@mui/icons-material/CancelOutlined";
import { useRig } from "@flyball/react";
import { humanise } from "@flyball/client";
import { stepOf } from "../model.js";
import { hashFor } from "../router.js";
import { useEventsData, useRigData } from "../dashboard/context.js";
import { humaniseSubject, TIME } from "./Events.js";
import { rowsThatFit } from "./size.js";
import type { WidgetType, WidgetComponentProps } from "./types.js";

/** The status row (28px), the progress bar (4px) and the two gaps between them and the list (dashboard.css `.dash-program`). */
const HEAD_PX = 28 + 8 + 4 + 8;
/** One event line: 20px line-height, no padding. */
const ROW_PX = 20;

/**
 * State chip · program · step, a Cancel button while running, the
 * progress bar, then as many recent program events as the body holds (never
 * a scrollbar). Body only; the frame is `WidgetFrame`'s.
 */
const ProgramWidget = memo(function ProgramWidget({ config, widget }: WidgetComponentProps) {
  const rig = useRig();
  const { programmer, rowHeight, canWrite } = useRigData();
  const events = useEventsData();
  const [busy, setBusy] = useState(false);
  const p = programmer.data;
  const running = p?.running ?? false;
  // Stays set (with the error) until the next run clears it, even once `running` goes false.
  const failed = p?.failed ?? false;
  const shown = Math.min(Math.max(0, Number(config.events ?? 5)), rowsThatFit(widget.h, rowHeight, true, HEAD_PX, ROW_PX));
  const recent = events.filter((e) => e.subject_kind === "program").slice(-shown).reverse();
  // The programmer says step and command; the program's name is in the step events' subject (`anneal[4]`).
  const latest = [...events].reverse().find((e) => e.subject_kind === "program" && e.code === "step");
  const name = latest ? latest.subject.replace(/\[\d+\]$/, "") : null;
  const cancel = () => {
    setBusy(true);
    rig
      .cancelProgram()
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
          {!p ? "…" : running ? `step ${stepOf(p)}${p.type ? ` · ${humanise(p.type)}` : ""}` : failed ? `failed${p.error ? ` · ${p.error}` : ""}` : "nothing is running"}
        </Typography>
        {running && config.cancel !== false && (
          <Button size="small" variant="outlined" color="error" startIcon={<CancelIcon />} onClick={cancel} disabled={busy || !canWrite} sx={{ ml: "auto", flex: "none" }}>
            Cancel
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
              <Typography variant="body2" component="span" color={e.severity === "error" ? "error" : e.severity === "warning" ? "warning.main" : "text.secondary"} noWrap>
                {humanise(e.code)}
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

export const program: WidgetType = {
  type: "program",
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
      cancel: { type: "boolean", title: "Cancel button", default: true },
    },
  }),
  defaultConfig: () => ({ events: 5, cancel: true }),
  labelFor: () => "Program",
  Component: ProgramWidget,
};
