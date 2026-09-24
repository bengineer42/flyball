import type { MouseEvent, ReactNode } from "react";
import { Box, Button, Chip, Link, Paper, Stack, Table, TableBody, TableCell, TableRow, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import type { DeviceOut, RunOut, Severity } from "@flyball/client";
import { atLeast, describeDevice, describeNamespace, describeSignal, describeUnit, isNamespace, publishes, signalsOf, writable, type TreeNode } from "@flyball/client";
import { PAGE_ICONS, type IconComponent } from "./icons.js";
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

/** A device card: label (a link to its page; the name is its hover hint), type, a status chip at the end, then the body. The whole card opens the page when it has one. */
export function DeviceCard({ icon: Icon, name, label, href, type, chip, actions, children, footer, className }: { icon: IconComponent; name: string; label?: string | null; href?: string; type: string; chip: ReactNode; actions?: ReactNode; children: ReactNode; footer?: ReactNode; className?: string }) {
  return (
    <Paper className={className} sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.5, minWidth: 0, ...(href ? clickableSx : {}) }} onClick={clickThrough(href)}>
      {/* The name and its status on one row; the driver's kind beneath, where it never fights the name for room. */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ minWidth: 0 }}>
        <Icon fontSize="small" sx={{ color: "text.disabled", flex: "none" }} />
        <Typography fontWeight={600} noWrap sx={{ minWidth: 0 }}>
          {href ? (
            <Link href={href} underline="hover" color="inherit" title={name}>
              {label ?? name}
            </Link>
          ) : (
            <span title={name}>{label ?? name}</span>
          )}
        </Typography>
        <Box sx={{ ml: "auto !important", flex: "none" }}>{chip}</Box>
        {actions}
      </Stack>
      <Typography variant="body2" color="text.secondary" noWrap title={`${name} · ${type}`} sx={{ mt: -1 }}>
        {type}
      </Typography>
      {children}
      {footer && (
        <Typography variant="body2" color="text.secondary">
          {footer}
        </Typography>
      )}
    </Paper>
  );
}

/** The worst of a device's conditions as a dot tone: red at `error`, amber at `warning`, grey otherwise. */
export function conditionTone(conditions: ReadonlyArray<{ severity: Severity }>): "ok" | "warn" | "alarm" {
  return conditions.some((c) => atLeast(c.severity, "error")) ? "alarm" : conditions.some((c) => atLeast(c.severity, "warning")) ? "warn" : "ok";
}

/** `[RP]`-style access flags for a hover hint: what a signal supports, after the address. */
export const accessFlags = (access: string) => `[${access.toUpperCase()}]`;

/** A signal as a chip: its label and unit, linking to its page; the hint carries the address, access and quantity. */
function SignalChip({ signal }: { signal: Extract<TreeNode, { access: string }> }) {
  return (
    <Chip
      variant="outlined"
      clickable
      component="a"
      href={hrefFor({ kind: "signal", name: signal.address })}
      label={[describeSignal(signal), describeUnit(signal.unit)].filter(Boolean).join(" ")}
      title={`${signal.address} ${accessFlags(signal.access)}: ${signal.quantity}${signal.dimension ? ` (${signal.dimension})` : ""}`}
      sx={writable(signal) && !publishes(signal) ? { borderStyle: "dashed" } : undefined}
    />
  );
}

/** One row of the tree per top-level entry: a signal alone, or a namespace with its signals as chips. */
function TreeRows({ device }: { device: DeviceOut }) {
  const rows: Array<{ key: string; label: ReactNode; signals: ReturnType<typeof signalsOf> }> = [];
  const loose = device.signals.filter((n) => !isNamespace(n));
  if (loose.length) rows.push({ key: "", label: null, signals: signalsOf(loose) });
  for (const node of device.signals) {
    if (!isNamespace(node)) continue;
    rows.push({ key: node.address, label: describeNamespace(node), signals: signalsOf(node.signals) });
  }
  return (
    // Fixed layout: a long namespace label ("Supply humidities when unbound") wraps in its column rather than pushing the table out of the card.
    <Table size="small" sx={{ tableLayout: "fixed", width: "100%", "& td": { border: 0, px: 0, py: 0.75 } }}>
      {rows.some((r) => r.label !== null) && (
        <colgroup>
          <col style={{ width: "26%" }} />
          <col />
        </colgroup>
      )}
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.key}>
            {row.label !== null && (
              <TableCell sx={{ pr: "12px !important", fontWeight: 500, verticalAlign: "top", overflowWrap: "anywhere" }} title={row.key}>
                {row.label}
              </TableCell>
            )}
            <TableCell colSpan={row.label === null ? 2 : 1}>
              <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.5}>
                {row.signals.map((signal) => (
                  <SignalChip key={signal.address} signal={signal} />
                ))}
              </Stack>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/**
 * A device: its run state as a dot (stopped or a condition in colour), one
 * row per namespace with the signals as chips (a dashed chip is write-only),
 * the poll period and the last read. `run` is the live one from `/ws/devices`
 * when the caller has it, else what `GET /api/devices` said.
 */
export function DeviceSummaryCard({ device, run, link = true, actions, className }: { device: DeviceOut; run?: (Partial<RunOut> & { conditions?: ReadonlyArray<{ code: string; severity: Severity; message: string }> }) | null; link?: boolean; actions?: ReactNode; className?: string }) {
  const live = run ?? device.run;
  const conditions = run?.conditions ?? device.conditions;
  const polled = live !== null && live !== undefined;
  const stopped = polled && live.running === false;
  const tone = stopped ? "warn" : conditionTone(conditions);
  const footer = polled ? [live.period_s != null && `every ${live.period_s} s`, live.last_read_ns != null && `last read ${clock(live.last_read_ns)}`].filter(Boolean).join(" · ") : null;
  const named = conditions.map((c) => c.code).join(", ");
  return (
    <DeviceCard
      className={className}
      icon={PAGE_ICONS.devices}
      name={device.name}
      label={device.label}
      href={link ? hrefFor({ kind: "device", name: device.name }) : undefined}
      type={describeDevice(device.driver ?? device.class_name)}
      chip={<StatusDot tone={tone} label={stopped ? "stopped" : named || undefined} title={stopped ? "polling stopped" : conditions.map((c) => `${c.code}: ${c.message}`).join("\n") || (polled ? "running" : "not polled")} />}
      actions={actions}
      footer={footer || undefined}
    >
      {device.signals.length > 0 ? (
        <TreeRows device={device} />
      ) : (
        <Typography variant="body2" color="text.secondary">
          declares no signals
        </Typography>
      )}
    </DeviceCard>
  );
}
