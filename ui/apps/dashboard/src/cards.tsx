import type { MouseEvent, ReactNode } from "react";
import { Box, Chip, Link, Paper, Stack, Table, TableBody, TableCell, TableRow, Typography } from "@mui/material";
import type { ReaderRun, ReaderSchema } from "@flyball/client";
import { SourceIcon, type IconComponent } from "./icons.js";
import { hrefFor } from "./router.js";
import { clock } from "./time.js";

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

/** A device card: name (a link to its page), type, a status chip at the end, then the body. The whole card opens the page when it has one. */
export function DeviceCard({ icon: Icon, name, href, type, chip, children, footer }: { icon: IconComponent; name: string; href?: string; type: string; chip: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <Paper sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1, ...(href ? clickableSx : {}) }} onClick={clickThrough(href)}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Icon fontSize="small" sx={{ color: "text.disabled" }} />
        <Typography fontWeight={600}>
          {href ? (
            <Link href={href} underline="hover" color="inherit">
              {name}
            </Link>
          ) : (
            name
          )}
        </Typography>
        <Typography variant="body2" color="text.secondary" noWrap>
          {type}
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
export function ReaderCard({ reader, run, link = true }: { reader: ReaderSchema; run: Partial<ReaderRun> | undefined; link?: boolean }) {
  const footer = run
    ? [run.period_s != null && `every ${run.period_s} s`, run.last_read_ns != null && `last read ${clock(run.last_read_ns)}`].filter(Boolean).join(" · ")
    : null;
  return (
    <DeviceCard
      icon={SourceIcon}
      name={reader.name}
      href={link ? hrefFor({ kind: "reader", name: reader.name }) : undefined}
      type={reader.type}
      chip={
        <Chip
          label={run ? (run.running ? "running" : "stopped") : "…"}
          color={run ? (run.running ? "success" : "warning") : "default"}
          variant="outlined"
        />
      }
      footer={footer || undefined}
    >
      {reader.sources.length > 0 ? (
        <Table size="small" sx={{ "& td": { border: 0, px: 0, py: 0.5 } }}>
          <TableBody>
            {reader.sources.map((src) => (
              <TableRow key={src.name}>
                <TableCell sx={{ width: "1%", whiteSpace: "nowrap", pr: "12px !important", fontWeight: 500 }}>
                  <Link href={hrefFor({ kind: "source", name: src.name })} underline="hover" color="inherit">
                    {src.name}
                  </Link>
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
