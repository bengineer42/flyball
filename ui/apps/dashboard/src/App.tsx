import { createContext, memo, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Typography } from "@mui/material";
import { LinksProvider, SignalPrompt, countRender, useSignals, useRigSchema, useSources, useRecording, useEvents, useQuery, useRig, useSimulation, useStreamStatus, type YScale } from "@flyball/react";
import type { DeviceState, RigSchema, SourceOut } from "@flyball/client";
import { Shell } from "./Shell.js";
import { PAGES, hashFor, hrefFor, legacyControllerRedirect, useRoute, useScrollMemory, type Page } from "./router.js";
import { Status, SimChip } from "./Status.js";
import { Overview } from "./pages/Overview.js";
import { Dashboards } from "./pages/Dashboards.js";
import { DashboardSwitcher } from "./dashboard/DashboardSwitcher.js";
import { Sources, SourceDetail, ChannelDetail } from "./pages/Sources.js";
import { Graph } from "./pages/Graph.js";
import { ReaderDetail, Readers } from "./pages/Devices.js";
import { Controllers } from "./pages/Controllers.js";
import { Events } from "./pages/Events.js";
import { Sessions } from "./pages/Sessions.js";
import { Programs, ProgramDetail } from "./pages/Programs.js";
import { Simulation } from "./pages/Simulation.js";
import { readYScale, writeYScale, readEvery, writeEvery, type ChartSettings } from "./YScaleSelect.js";
import { readHome } from "./dashboard/home.js";
import type { Programmer, Recording } from "./model.js";

const SimulationPage = memo(Simulation);

const PAGE_LABEL: Record<Page, string> = Object.fromEntries(PAGES.map((p) => [p.id, p.label])) as Record<Page, string>;
PAGE_LABEL.readers = "Readers";
// `#/loops`/`#/actuators` redirect to `#/controllers` before this is ever shown; set only so a
// render in the instant before that effect fires never reads `undefined` off `PAGE_LABEL`.
PAGE_LABEL.loops = "Controllers";
PAGE_LABEL.actuators = "Controllers";

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

const NO_STATES: Record<string, DeviceState> = {};

/** The app bar's chips: the polled state plus the store's stream health. */
function AppStatus({ schema }: { schema: RigSchema }) {
  const { recording, programmer, simulationSpeed } = useLive();
  const simulated = useContext(SimulatedContext);
  const { streams } = useStreamStatus();
  return (
    <>
      <Status actuators={Object.values(schema.actuators)} states={NO_STATES} recording={recording} programmer={programmer} streams={streams} />
      {simulated && <SimChip speed={simulationSpeed} />}
    </>
  );
}

/** A program waiting on a person shows on every page: nobody should have to go looking for the Go button. */
function Signals() {
  const signals = useSignals();
  return <SignalPrompt signals={signals.pending} onFire={signals.fire} onInterrupt={signals.interrupt} />;
}

// Pages that need the polled or streamed state subscribe here, one wrapper each, so
// a tick re-renders the page that shows it and nothing above.

function DashboardsPage(props: { name: string | null; generated: boolean; schema: RigSchema; sources: SourceOut[]; onOpen(name: string | null, generated?: boolean): void } & ChartSettings) {
  const { recording, programmer } = useLive();
  const { events } = useEvents(500);
  // Widgets read samples, loops and actuator states from the telemetry store themselves.
  return <Dashboards {...props} traces={NO_TRACES} states={NO_STATES} events={events} recording={recording} programmer={programmer} />;
}
const NO_TRACES = {};

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
 * The app: one rig document and a page per section. Nothing here knows what
 * the rig is, and nothing here re-renders on a sample: live values reach the
 * pages through the telemetry store (`useTraceRef`, `useLatest`, ...) and the
 * polled state through `LiveProvider`.
 */
export function App() {
  countRender("App");
  const [route, navigate] = useRoute();
  const { page, name, measurand, params } = route;
  // A bare `#/` opens the home dashboard when one is set; the Overview stays a click away.
  useEffect(() => {
    if (/^#?\/?$/.test(window.location.hash) && readHome()) window.location.replace(hashFor("dashboards"));
  }, []);
  // The Loops and Actuators pages merged into Controllers (a loop is always bound to exactly one
  // actuator); an old `#/loops[/x]` or `#/actuators[/x]` link still works, redirected here.
  useEffect(() => {
    const to = legacyControllerRedirect(route);
    if (to) window.location.replace(to);
  }, [route]);
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
  const schema = useRigSchema();
  const sources = useSources();
  const ready = Boolean(schema.data && sources.data);
  useScrollMemory(ready);

  if (schema.error)
    return (
      <Alert severity="error" sx={{ m: 3 }}>
        Cannot reach the rig: {schema.error.message}
      </Alert>
    );
  if (!schema.data || !sources.data)
    return (
      <Typography color="text.secondary" sx={{ m: 3 }}>
        loading…
      </Typography>
    );

  const rig = schema.data;
  const title = name === null ? PAGE_LABEL[page] : measurand ? `${name}.${measurand}` : page === "sessions" ? `Session #${name}` : name;
  const source = name === null ? undefined : sources.data.find((s) => s.name === name);
  const charts = { windowS, onWindow: setWindowS, yScale, onYScale: setYScale, every, onEvery: setEvery };
  const openDashboard = (n: string | null, generated?: boolean) => (window.location.hash = hashFor("dashboards", n, null, generated ? { generated: "" } : {}));

  return (
    <LinksProvider hrefFor={hrefFor}>
      <LiveProvider>
        <SimulatedContext.Consumer>
          {(simulated) => (
            <Shell
              page={page === "readers" ? "overview" : page === "loops" || page === "actuators" ? "controllers" : page}
              onNavigate={navigate}
              title={title}
              status={<AppStatus schema={rig} />}
              simulated={simulated}
              startSlot={page === "dashboards" ? <DashboardSwitcher name={name} generated={"generated" in params} onOpen={openDashboard} /> : undefined}
            >
              <Signals />
              {page === "overview" && <Overview schema={rig} sources={sources.data!} onOpen={navigate} {...charts} />}
              {page === "dashboards" && <DashboardsPage name={name} generated={"generated" in params} schema={rig} sources={sources.data!} onOpen={openDashboard} {...charts} />}
              {page === "sources" && name === null && <Sources sources={sources.data!} {...charts} />}
              {page === "sources" && name !== null && measurand === null && <SourceDetail source={source} {...charts} />}
              {page === "sources" && name !== null && measurand !== null && <ChannelDetail source={source} measurand={measurand} {...charts} />}
              {page === "graph" && <Graph sources={sources.data!} {...charts} />}
              {page === "readers" && name === null && <Readers schema={rig} />}
              {page === "readers" && name !== null && <ReaderDetail schema={rig} name={name} />}
              {page === "controllers" && <Controllers {...charts} name={name} />}
              {page === "programs" && <ProgramsPage name={name} navigate={navigate} />}
              {page === "events" && <EventsPage level={params.level} />}
              {page === "sessions" && <SessionsPage name={name} navigate={navigate} />}
              {page === "simulation" && <SimulationPage />}
            </Shell>
          )}
        </SimulatedContext.Consumer>
      </LiveProvider>
    </LinksProvider>
  );
}
