import type { MouseEvent, ReactNode } from "react";
import { Box, Button, Chip, Link, Paper, Stack, Table, TableBody, TableCell, TableRow, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import type { ReaderRun, ReaderSchema } from "@flyball/client";
import { describeDevice } from "@flyball/client";
import { SourceIcon, type IconComponent } from "./icons.js";
import { hrefFor } from "./router.js";
import { clock } from "./time.js";

/** A dot matching `PanelFrame`'s status dot (`packages/react/src/styles.css`): grey normal, amber/red abnormal.
 * Quiet by default (DESIGN-SPEC.md §0/§2) — text stays only for what needs reading, e.g. "stopped". */
export function StatusDot({ tone = "ok", label, title }: { tone?: "ok" | "warn" | "alarm"; label?: ReactNode; title?: string }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} title={title}>
      <span className={`fb-panel-dot fb-panel-dot-${tone}`} />
      {label && (
        <Typography variant="body2" color="text.secondary">
          {label}
        </Typography>
      )}
    </Stack>
  );
}

/**
 * A section's heading on a list page (DESIGN-SPEC.md §2 "Section headers"):
 * icon, 11px uppercase title, a muted count, and right-aligned content —
 * one component so every page's list section reads the same.
 */
export function SectionHead({ icon: Icon, title, count, end }: { icon: IconComponent; title: string; count?: number; end?: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} className="section-head" flexWrap="wrap" useFlexGap sx={{ minHeight: 32, mb: 1 }}>
      <Icon fontSize="small" sx={{ color: "text.disabled" }} />
      <Typography component="h2" sx={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", color: "text.secondary" }}>
        {title}
      </Typography>
      {count !== undefined && (
        <Typography variant="body2" color="text.secondary">
          · {count}
        </Typography>
      )}
      {end && (
        <Box sx={{ ml: "auto !important", display: "flex", alignItems: "center", gap: 1 }}>
          {end}
        </Box>
      )}
    </Stack>
  );
}

export type StateBlockState = "loading" | "empty" | "unbound" | "error" | "stale";

/**
 * One sentence explaining why a list is empty, loading, unbound, stale or
 * broken, with an optional action — DESIGN-SPEC.md §2 "Empty / loading /
 * error states". Dashed border, `bg-2`, centred; used on every list page.
 */
export function StateBlock({ state, message, action }: { state: StateBlockState; message: ReactNode; action?: { label: string; onClick?: () => void; href?: string } }) {
  const isError = state === "error";
  return (
    <Box
      data-testid={`state-block-${state}`}
      role={isError ? "alert" : "status"}
      sx={{
        border: "1px dashed",
        borderColor: isError ? "error.main" : "divider",
        borderRadius: 1,
        bgcolor: isError ? (t) => alpha(t.palette.error.main, 0.08) : "action.hover",
        p: 4,
        textAlign: "center",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 1.5,
      }}
    >
      <Typography variant="body2" color={isError ? "error" : "text.secondary"}>
        {message}
      </Typography>
      {action &&
        (action.href ? (
          <Button size="small" variant="outlined" component="a" href={action.href}>
            {action.label}
          </Button>
        ) : (
          <Button size="small" variant="outlined" onClick={action.onClick}>
            {action.label}
          </Button>
        ))}
    </Box>
  );
}

/** True when the click landed on something interactive of its own: a link, a button, a field. */
export const onControl = (e: MouseEvent) => Boolean((e.target as Element).closest("a, button, input, select, textarea, [role=button]"));

/**
 * Open `href` on a click anywhere in an element that also holds its own
 * links and buttons (an `<a>` cannot wrap those). Plain click navigates;
 * modified clicks (new tab) are left to the real link in the header.
 */
export const clickThrough = (href: string | undefined) => (e: MouseEvent) => {
  if (!href || onControl(e) || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  window.location.hash = href;
};

/** The look of a surface that opens a page when clicked. */
export const clickableSx = { cursor: "pointer", transition: "border-color 120ms, background-color 120ms", "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" } } as const;

/** A device card: label (a link to its page) with the name beside it when they differ, type, a status chip at the end, then the body. The whole card opens the page when it has one. */
export function DeviceCard({ icon: Icon, name, label, href, type, chip, children, footer, className }: { icon: IconComponent; name: string; label?: string | null; href?: string; type: string; chip: ReactNode; children: ReactNode; footer?: ReactNode; className?: string }) {
  return (
    <Paper className={className} sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.5, minWidth: 0, ...(href ? clickableSx : {}) }} onClick={clickThrough(href)}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Icon fontSize="small" sx={{ color: "text.disabled" }} />
        <Typography fontWeight={600}>
          {href ? (
            <Link href={href} underline="hover" color="inherit">
              {label ?? name}
            </Link>
          ) : (
            label ?? name
          )}
        </Typography>
        <Typography variant="body2" color="text.secondary" noWrap title={label ? `${name} · ${type}` : type}>
          {label ? `${name} · ${type}` : type}
        </Typography>
        <Box sx={{ ml: "auto !important" }}>{chip}</Box>
      </Stack>
      {children}
      {footer && (
        <Typography variant="body2" color="text.secondary">
          {footer}
        </Typography>
      )}
    </Paper>
  );
}

/** A reader: run state, one row per source it declares with its measurands as chips, read period and last read. */
export function ReaderCard({ reader, run, link = true, className }: { reader: ReaderSchema; run: Partial<ReaderRun> | undefined; link?: boolean; className?: string }) {
  const footer = run
    ? [run.period_s != null && `every ${run.period_s} s`, run.last_read_ns != null && `last read ${clock(run.last_read_ns)}`].filter(Boolean).join(" · ")
    : null;
  return (
    <DeviceCard
      className={className}
      icon={SourceIcon}
      name={reader.name}
      label={reader.label}
      href={link ? hrefFor({ kind: "reader", name: reader.name }) : undefined}
      type={describeDevice(reader.type)}
      chip={run ? <StatusDot tone={run.running ? "ok" : "warn"} label={run.running ? undefined : "stopped"} title={run.running ? "running" : "stopped"} /> : <StatusDot label="…" />}
      footer={footer || undefined}
    >
      {reader.sources.length > 0 ? (
        <Table size="small" sx={{ "& td": { border: 0, px: 0, py: 0.75 } }}>
          <TableBody>
            {reader.sources.map((src) => (
              <TableRow key={src.name}>
                <TableCell sx={{ width: "1%", whiteSpace: "nowrap", pr: "12px !important", fontWeight: 500 }}>
                  <Link href={hrefFor({ kind: "source", name: src.name })} underline="hover" color="inherit" title={src.label ? src.name : undefined}>
                    {src.label ?? src.name}
                  </Link>
                  {src.label && (
                    <Typography component="span" variant="body2" color="text.secondary">
                      {" "}
                      {src.name}
                    </Typography>
                  )}
                </TableCell>
                <TableCell>
                  <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.5}>
                    {Object.entries(src.measurands).map(([key, m]) => (
                      <Chip
                        key={key}
                        variant="outlined"
                        clickable
                        component="a"
                        href={hrefFor({ kind: "channel", name: src.name, measurand: key })}
                        label={`${m.label || key} ${m.unit}`}
                        title={`${key}: ${m.dimension}`}
                      />
                    ))}
                  </Stack>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Typography variant="body2" color="text.secondary">
          declares no sources
        </Typography>
      )}
    </DeviceCard>
  );
}
