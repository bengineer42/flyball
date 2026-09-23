/**
 * Options (D-053): what the sidebar used to reach and the app bar has no room for, behind the gear.
 * One tab per concern, the tab in the route (`#/options/<tab>`), so each is linkable. For now:
 * the rig file (today's Config page, whole), appearance, and every page not reached from the bar.
 * The dashboards list, the runner and access get their own tabs in a later step of the redesign.
 */
import { lazy, Suspense } from "react";
import { Box, Card, CardActionArea, CardContent, FormControlLabel, Radio, RadioGroup, Tab, Tabs, Typography } from "@mui/material";
import { PAGE_ICONS } from "../icons.js";
import { hashFor, PAGES, type Page } from "../router.js";
import { useColorMode, type ColorChoice } from "../theme.js";
import { OPTION_TABS, type OptionTab } from "./optionTabs.js";

const RigPage = lazy(() => import("./Rig.js").then((m) => ({ default: m.RigPage })));

export function Options({ tab, simulated, onTab }: { tab: OptionTab; simulated: boolean; onTab(tab: OptionTab): void }) {
  return (
    <Box>
      <Tabs value={tab} onChange={(_, v: OptionTab) => onTab(v)} aria-label="options" sx={{ mb: 2.5, borderBottom: 1, borderColor: "divider" }} data-testid="options-tabs">
        {OPTION_TABS.map((t) => (
          <Tab key={t.id} value={t.id} label={t.label} data-testid={`options-tab-${t.id}`} sx={{ textTransform: "none" }} />
        ))}
      </Tabs>
      {tab === "rig" && (
        <Suspense fallback={<Typography color="text.secondary">loading…</Typography>}>
          <RigPage />
        </Suspense>
      )}
      {tab === "appearance" && <Appearance />}
      {tab === "pages" && <Pages simulated={simulated} />}
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
  inputs: "Every published signal, its value and trend: the plain fall-back view.",
  devices: "Every device with its signals and commands.",
  controllers: "Every control loop's faceplate.",
  graph: "Several signals and setpoints on one chart.",
  overview: "The rig's summary, generated from its devices.",
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
