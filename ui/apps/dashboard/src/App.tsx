import { createContext, memo, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Typography } from "@mui/material";
import { LinksProvider, WaitPrompt, countRender, useWaits, useDevices, useRecording, useEvents, useQuery, useRig, useSimulation, useStreamStatus, useNowS, type YScale } from "@flyball/react";
import { RigError, type DeviceOut } from "@flyball/client";
import { Shell } from "./Shell.js";
import { TokenChip, TokenPrompt } from "./TokenChip.js";
import { PAGES, hashFor, hrefFor, useRoute, useScrollMemory, type Page } from "./router.js";
import { Status, SimChip } from "./Status.js";
import { Overview } from "./pages/Overview.js";
import { Dashboards } from "./pages/Dashboards.js";
import { DashboardSwitcher } from "./dashboard/DashboardSwitcher.js";
import { Inputs, SignalDetail } from "./pages/Inputs.js";
import { Graph } from "./pages/Graph.js";
import { DevicePage } from "./pages/Devices.js";
import { RigPage } from "./pages/Rig.js";
import { Controllers } from "./pages/Controllers.js";
import { Events } from "./pages/Events.js";
import { Sessions } from "./pages/Sessions.js";
import { Programs, ProgramDetail } from "./pages/Programs.js";
import { Simulation } from "./pages/Simulation.js";
import { readYScale, writeYScale, readEvery, writeEvery, type ChartSettings } from "./YScaleSelect.js";
import { readHome } from "./dashboard/home.js";
import type { Programmer, Recording } from "./model.js";

const SimulationPage = memo(Simulation);

const PAGE_LABEL: Record<Page, string> = { ...(Object.fromEntries(PAGES.map((p) => [p.id, p.label])) as Record<Page, string>), devices: "Devices", inputs: "Inputs" };

/**
 * What is polled rather than streamed, held once for the app bar and the
 * pages that show it. Lives below `App` so a poll re-renders only the
 * components that read it, never the whole tree; `simulated` has its own
 * context because it is a boolean that hardly ever changes, and the shell
 * reads it on every page.
 */
interface Live {
  recording: Recording;
  programmer: Programmer;
  simulationSpeed: number | undefined;
}
const LiveContext = createContext<Live | null>(null);
const SimulatedContext = createContext(false);
const useLive = () => {
  const live = useContext(LiveContext);
  if (!live) throw new Error("useLive: outside <LiveProvider>");
  return live;
};

function LiveProvider({ children }: { children: ReactNode }) {
  const rigClient = useRig();
  const recording = useRecording(5000);
  const simulation = useSimulation();
  // Poll the programmer every second while a program runs, every five otherwise.
  const [programRunning, setProgramRunning] = useState(false);
  const programmer = useQuery(
    async () => {
      const p = await rigClient.programmer();
      setProgramRunning(p.running);
      return p;
    },
    [rigClient],
    { refreshMs: programRunning ? 1000 : 5000 },
  );
  const value = useMemo<Live>(() => ({ recording, programmer, simulationSpeed: simulation.speed }), [recording, programmer, simulation.speed]);
  return (
    <SimulatedContext.Provider value={simulation.attached}>
      <LiveContext.Provider value={value}>{children}</LiveContext.Provider>
    </SimulatedContext.Provider>
  );
}

/** The app bar's chips: the polled state plus the store's stream health. */
function AppStatus() {
  const { recording, programmer, simulationSpeed } = useLive();
  const simulated = useContext(SimulatedContext);
  const { streams } = useStreamStatus();
  return (
    <>
      <Status recording={recording} programmer={programmer} streams={streams} />
      {simulated && <SimChip speed={simulationSpeed} />}
      <TokenChip />
    </>
  );
}

/** A program waiting on a person shows on every page: nobody should have to go looking for the Go button. */
function Waits() {
  const waits = useWaits();
  const nowS = useNowS();
  return <WaitPrompt waits={waits.pending} nowS={nowS} onFire={waits.fire} onInterrupt={waits.interrupt} />;
}

// Pages that need the polled or streamed state subscribe here, one wrapper each, so
// a tick re-renders the page that shows it and nothing above.

function DashboardsPage(props: { name: string | null; generated: boolean; devices: DeviceOut[]; onOpen(name: string | null, generated?: boolean): void } & ChartSettings) {
  const { recording, programmer } = useLive();
  const { events } = useEvents(500);
  // Widgets read samples, controllers and write states from the telemetry store themselves.
  return <Dashboards {...props} events={events} recording={recording} programmer={programmer} />;
}

function ProgramsPage({ name, navigate }: { name: string | null; navigate: (page: Page, name?: string) => void }) {
  const { programmer } = useLive();
  const { events } = useEvents(500);
  if (name === null) return <Programs programmer={programmer} events={events} onOpen={(n) => navigate("programs", n)} />;
  return <ProgramDetail key={name} name={name} programmer={programmer} events={events} onSaved={(n) => navigate("programs", n)} onDeleted={() => navigate("programs")} />;
}

function EventsPage({ level }: { level: string | undefined }) {
  const { events, error } = useEvents(500);
  return <Events events={events} error={error} level={level} />;
}

function SessionsPage({ name, navigate }: { name: string | null; navigate: (page: Page, name?: number | null) => void }) {
  const { recording } = useLive();
  return <Sessions recording={recording} selected={name !== null && /^\d+$/.test(name) ? Number(name) : null} onSelect={(i) => navigate("sessions", i)} />;
}

/**
 * The app: one rig document (`GET /api/devices`: every device with its
 * signal tree) and a page per section. Nothing here knows what the rig is,
 * and nothing here re-renders on a sample: live values reach the pages
 * through the telemetry store (`useTraceRef`, `useSignal`, ...) and the
 * polled state through `LiveProvider`.
 */
export function App() {
  countRender("App");
  const [route, navigate] = useRoute();
  const { page, name, params } = route;
  // A bare `#/` opens the home dashboard when one is set; the Overview stays a click away.
  useEffect(() => {
    if (/^#?\/?$/.test(window.location.hash) && readHome()) window.location.replace(hashFor("dashboards"));
  }, []);
  const [windowS, setWindowS] = useState(300);
  const [yScale, setYScaleState] = useState<YScale>(readYScale);
  const setYScale = useCallback((s: YScale) => {
    setYScaleState(s);
    writeYScale(s);
  }, []);
  const [every, setEveryState] = useState<number>(readEvery);
  const setEvery = useCallback((n: number) => {
    setEveryState(n);
    writeEvery(n);
  }, []);
  const devices = useDevices();
  const ready = Boolean(devices.data);
  useScrollMemory(ready);

  if (devices.error) {
    // A daemon started with `--token` refuses everything without one: the first request the app makes
    // (this one) is what discovers that, so it is where the prompt replaces the usual error.
    if (devices.error instanceof RigError && devices.error.status === 401) return <TokenPrompt />;
    return (
      <Alert severity="error" sx={{ m: 3 }}>
        Cannot reach the rig: {devices.error.message}
      </Alert>
    );
  }
  if (!devices.data)
    return (
      <Typography color="text.secondary" sx={{ m: 3 }}>
        loading…
      </Typography>
    );

  const all = devices.data;
  const title = name === null ? PAGE_LABEL[page] : page === "sessions" ? `Session #${name}` : name;
  const charts = { windowS, onWindow: setWindowS, yScale, onYScale: setYScale, every, onEvery: setEvery };
  const openDashboard = (n: string | null, generated?: boolean) => (window.location.hash = hashFor("dashboards", n, generated ? { generated: "" } : {}));

  return (
    <LinksProvider hrefFor={hrefFor}>
      <LiveProvider>
        <SimulatedContext.Consumer>
          {(simulated) => (
            <Shell
              page={page}
              onNavigate={navigate}
              title={title}
              status={<AppStatus />}
              simulated={simulated}
              startSlot={page === "dashboards" ? <DashboardSwitcher name={name} generated={"generated" in params} onOpen={openDashboard} /> : undefined}
            >
              <Waits />
              {page === "overview" && <Overview devices={all} onOpen={navigate} {...charts} />}
              {page === "dashboards" && <DashboardsPage name={name} generated={"generated" in params} devices={all} onOpen={openDashboard} {...charts} />}
              {page === "inputs" && name === null && <Inputs devices={all} {...charts} />}
              {page === "inputs" && name !== null && <SignalDetail devices={all} address={name} {...charts} />}
              {page === "graph" && <Graph devices={all} {...charts} />}
              {page === "devices" && name === null && <Inputs devices={all} {...charts} />}
              {page === "devices" && name !== null && <DevicePage devices={all} name={name} {...charts} />}
              {page === "rig" && <RigPage />}
              {page === "controllers" && <Controllers devices={all} name={name} {...charts} />}
              {page === "programs" && <ProgramsPage name={name} navigate={navigate} />}
              {page === "events" && <EventsPage level={params.level} />}
              {page === "sessions" && <SessionsPage name={name} navigate={navigate} />}
              {page === "simulation" && <SimulationPage devices={all} />}
            </Shell>
          )}
        </SimulatedContext.Consumer>
      </LiveProvider>
    </LinksProvider>
  );
}
