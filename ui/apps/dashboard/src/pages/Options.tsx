/**
 * Options (D-053): what the sidebar used to reach and the app bar has no room for, behind the gear.
 * One tab per concern, the tab in the route (`#/options/<tab>`), so each is linkable: this rig's
 * dashboards (order, read-only, home), the rig file (devices, links, controllers, the running
 * document), the runner (versions, save, shutdown/restart, a model's connection), access (who
 * this browser is here and what it may do), appearance, and every page not reached from the bar.
 */
import { lazy, Suspense, useState } from "react";
import { Alert, Box, Button, Card, CardActionArea, CardContent, Chip, FormControlLabel, IconButton, Link, Radio, RadioGroup, Switch, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, Tooltip, Typography } from "@mui/material";
import ArrowDownwardIcon from "@mui/icons-material/ArrowDownward";
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward";
import HomeIcon from "@mui/icons-material/Home";
import HomeOutlinedIcon from "@mui/icons-material/HomeOutlined";
import { invalidateDashboards, useDashboards, useRig } from "@flyball/react";
import type { DashboardRow } from "@flyball/client";
import { useAuth } from "../auth.js";
import { PasskeyManager } from "../PasskeyManager.js";
import { readHome, writeHome } from "../dashboard/home.js";
import { reorder, saveOrder } from "../dashboard/order.js";
import { PAGE_ICONS } from "../icons.js";
import { hashFor, PAGES, type Page } from "../router.js";
import { useColorMode, type ColorChoice } from "../theme.js";
import { OPTION_TABS, type OptionTab } from "./optionTabs.js";

const RigPage = lazy(() => import("./Rig.js").then((m) => ({ default: m.RigPage })));

export function Options({ tab, simulated, onTab, onSignIn }: { tab: OptionTab; simulated: boolean; onTab(tab: OptionTab): void; onSignIn(): void }) {
  return (
    <Box>
      <Tabs value={tab} onChange={(_, v: OptionTab) => onTab(v)} aria-label="options" sx={{ mb: 2.5, borderBottom: 1, borderColor: "divider" }} data-testid="options-tabs">
        {OPTION_TABS.map((t) => (
          <Tab key={t.id} value={t.id} label={t.label} data-testid={`options-tab-${t.id}`} sx={{ textTransform: "none" }} />
        ))}
      </Tabs>
      {tab === "dashboards" && <DashboardList />}
      {(tab === "rig" || tab === "runner") && (
        <Suspense fallback={<Typography color="text.secondary">loading…</Typography>}>
          <RigPage part={tab === "rig" ? "file" : "runner"} />
        </Suspense>
      )}
      {tab === "access" && <Access onSignIn={onSignIn} />}
      {tab === "appearance" && <Appearance />}
      {tab === "pages" && <Pages simulated={simulated} />}
    </Box>
  );
}

/**
 * This rig's saved dashboards in tab order: move one up or down (the same as dragging its tab),
 * make it read-only, make it the one `#/` opens. Each change is saved at once, as a new version of
 * that dashboard; home is this browser's alone. Renaming, deleting and editing stay on the
 * dashboard's own page.
 */
function DashboardList() {
  const rig = useRig();
  const { canOperate } = useAuth();
  const list = useDashboards();
  const rows = list.data ?? [];
  const [home, setHome] = useState(readHome);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (op: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await op();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      invalidateDashboards();
    }
  };
  const move = (from: number, to: number) => run(() => saveOrder(rig, rows, reorder(rows, from, to)));
  const setReadonly = (row: DashboardRow, readonly: boolean) => run(() => rig.saveDashboard(row.name, { ...row.body, readonly }));
  const toggleHome = (name: string) => {
    const next = home === name ? null : name;
    writeHome(next);
    setHome(next);
  };
  if (list.error) return <Alert severity="error">{list.error.message}</Alert>;
  if (!list.data) return <Typography color="text.secondary">loading…</Typography>;
  if (rows.length === 0)
    return (
      <Typography color="text.secondary">
        No saved dashboards yet. The generated overview is always first; <Link href={hashFor("dashboards")}>open it</Link> and Save as… to keep one, or use [+] beside the tabs.
      </Typography>
    );
  return (
    <Box sx={{ maxWidth: 900 }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      <Table size="small" data-testid="options-dashboards">
        <TableHead>
          <TableRow>
            <TableCell>Dashboard</TableCell>
            <TableCell>Place</TableCell>
            <TableCell>Read-only</TableCell>
            <TableCell>Home</TableCell>
            <TableCell>Saved</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={row.name} data-testid={`options-dashboard-${row.name}`}>
              <TableCell>
                <Link href={hashFor("dashboards", row.name)}>{row.name}</Link>
              </TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                <IconButton size="small" aria-label={`move ${row.name} earlier`} disabled={!canOperate || busy || i === 0} onClick={() => void move(i, i - 1)}>
                  <ArrowUpwardIcon fontSize="small" />
                </IconButton>
                <IconButton size="small" aria-label={`move ${row.name} later`} disabled={!canOperate || busy || i === rows.length - 1} onClick={() => void move(i, i + 1)}>
                  <ArrowDownwardIcon fontSize="small" />
                </IconButton>
              </TableCell>
              <TableCell>
                <Switch size="small" checked={row.body.readonly ?? false} disabled={!canOperate || busy} onChange={(e) => void setReadonly(row, e.target.checked)} inputProps={{ "aria-label": `${row.name} read-only` }} />
              </TableCell>
              <TableCell>
                <Tooltip title={home === row.name ? "Opened by #/ in this browser; click to unset" : "Open this at #/ in this browser"}>
                  <IconButton size="small" aria-label={`${row.name} home`} aria-pressed={home === row.name} onClick={() => toggleHome(row.name)}>
                    {home === row.name ? <HomeIcon fontSize="small" /> : <HomeOutlinedIcon fontSize="small" />}
                  </IconButton>
                </Tooltip>
              </TableCell>
              <TableCell sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>{new Date(row.created_ns / 1e6).toLocaleString()}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
        Order and read-only are saved on each dashboard, for everyone. Home is this browser's. Rename and delete a dashboard from its own page's ⋯ menu.
      </Typography>
    </Box>
  );
}

/** What each verb lets this browser do; an unknown one is shown by name. */
const VERB_NOTES: Record<string, string> = {
  read: "see the rig: values, events, sessions, programs, dashboards",
  operate: "drive it: commands, setpoints, controllers, programs, recording, the software stop, edits to the rig and dashboards",
};

/**
 * Who this browser is on this rig and what it may do, and the way in or out. Signing in itself is
 * the login page's (`Login.tsx`); this tab only opens it. Named tokens are managed at the front
 * (`flyball token`), not here yet.
 */
function Access({ onSignIn }: { onSignIn(): void }) {
  const { info, open, signedIn, canOperate, logout } = useAuth();
  const [passkeys, setPasskeys] = useState(false);
  if (!info) return <Typography color="text.secondary">loading…</Typography>;
  const who = info.user ? `${info.user.name} (${info.user.kind})` : "nobody: not signed in";
  return (
    <Box sx={{ maxWidth: 640, display: "grid", gap: 2 }} data-testid="options-access">
      <Box>
        <Typography variant="h2" sx={{ mb: 1 }}>
          This browser
        </Typography>
        <Typography data-testid="access-who">{open ? "Anyone who can reach this rig may use it: it asks nobody to sign in." : who}</Typography>
        <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", mt: 1 }}>
          {info.verbs.length === 0 && <Chip size="small" label="no access" color="warning" variant="outlined" />}
          {info.verbs.map((v) => (
            <Tooltip key={v} title={VERB_NOTES[v] ?? v}>
              <Chip size="small" label={v} variant="outlined" data-testid={`access-verb-${v}`} />
            </Tooltip>
          ))}
        </Box>
        {!canOperate && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            Controls that change the rig show greyed out.
          </Typography>
        )}
      </Box>
      {!open && (
        <Box sx={{ display: "flex", gap: 1 }}>
          {signedIn ? (
            <Button variant="outlined" onClick={() => void logout()} data-testid="access-sign-out">
              Sign out
            </Button>
          ) : (
            <Button variant="contained" onClick={onSignIn} data-testid="access-sign-in">
              Sign in
            </Button>
          )}
          {signedIn && info.login.passkey && typeof window !== "undefined" && "PublicKeyCredential" in window && (
            <Button variant="outlined" onClick={() => setPasskeys(true)}>
              Manage passkeys
            </Button>
          )}
        </Box>
      )}
      <Typography variant="body2" color="text.secondary">
        {!open && <>A caller with no credential gets {info.anonymous === "read" ? "read" : "nothing"} here. </>}Named tokens for scripts and models are made with <code>flyball token</code> at the rig's front.
      </Typography>
      <PasskeyManager open={passkeys} onClose={() => setPasskeys(false)} />
    </Box>
  );
}

function Appearance() {
  const { choice, choose } = useColorMode();
  return (
    <Box sx={{ maxWidth: 480 }}>
      <Typography variant="h2" sx={{ mb: 1 }}>
        Theme
      </Typography>
      <RadioGroup value={choice} onChange={(e) => choose(e.target.value as ColorChoice)} aria-label="theme" data-testid="theme-choice">
        <FormControlLabel value="system" control={<Radio size="small" />} label="Follow the system" />
        <FormControlLabel value="light" control={<Radio size="small" />} label="Light" />
        <FormControlLabel value="dark" control={<Radio size="small" />} label="Dark" />
      </RadioGroup>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        Kept in this browser only.
      </Typography>
    </Box>
  );
}

/** What each page is for, in a line: the Pages tab is the one place that lists them all. */
const PAGE_NOTES: Partial<Record<Page, string>> = {
  readings: "Every device and published signal, its value, trend and commands: the plain fall-back view.",
  controllers: "Every control loop's faceplate.",
  graph: "Several signals and setpoints on one chart.",
  programs: "Write and run programs. Also the program chip in the bar.",
  events: "What the rig reported. Also the conditions chip in the bar.",
  sessions: "Recorded sessions and their data. Also the recording chip in the bar.",
  simulation: "The simulated clock and plant. Also the sim chip in the bar.",
};

function Pages({ simulated }: { simulated: boolean }) {
  const shown = PAGES.filter((p) => p.id in PAGE_NOTES && (p.id !== "simulation" || simulated));
  return (
    <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }} data-testid="options-pages">
      {shown.map((p) => {
        const Icon = PAGE_ICONS[p.id];
        return (
          <Card key={p.id} variant="outlined">
            <CardActionArea href={hashFor(p.id)} data-testid={`options-page-${p.id}`}>
              <CardContent sx={{ display: "flex", gap: 1.5, alignItems: "flex-start" }}>
                <Icon fontSize="small" sx={{ mt: 0.25, color: "text.secondary" }} />
                <Box>
                  <Typography fontWeight={600}>{p.label}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {PAGE_NOTES[p.id]}
                  </Typography>
                </Box>
              </CardContent>
            </CardActionArea>
          </Card>
        );
      })}
    </Box>
  );
}
