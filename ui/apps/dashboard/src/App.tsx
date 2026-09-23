import { Component, createContext, lazy, memo, Suspense, useCallback, useContext, useEffect, useMemo, useState, type ErrorInfo, type ReactNode } from "react";
import { Alert, Typography } from "@mui/material";
import { ExposureBanner, LinksProvider, WaitPrompt, countRender, useWaits, useDevices, useRecording, useEvents, useUnreadEvents, useQuery, useRig, useSimulation, useStreamStatus, useNowS, useTelemetry, usePlayback, type PlaybackHook, type YScale } from "@flyball/react";
import { RigError, type DeviceOut, type RigEvent, deviceTitle, signalTitle, signalsOf } from "@flyball/client";
import { Shell } from "./Shell.js";
import { EventToasts } from "./EventToasts.js";
import { AuthChip, LoginPage } from "./Login.js";
import { useAuth } from "./auth.js";
import { StopButton } from "./StopButton.js";
import { PAGES, hashFor, hrefFor, useRoute, useScrollMemory, type Page } from "./router.js";
import { Status, SimChip, PausedChip } from "./Status.js";
import { readYScale, writeYScale, type ChartSettings } from "./YScaleSelect.js";
import { readHome } from "./dashboard/home.js";
import type { Programmer, Recording } from "./model.js";
import { useStartingRetry } from "./useStartingRetry.js";

// Each page (and the dashboard switcher) is its own chunk, fetched only on first visit to that
// route: the app shell, MUI and the telemetry store are the only things every route pays for.
const Overview = lazy(() => import("./pages/Overview.js").then((m) => ({ default: m.Overview })));
const Dashboards = lazy(() => import("./pages/Dashboards.js").then((m) => ({ default: m.Dashboards })));
const DashboardSwitcher = lazy(() => import("./dashboard/DashboardSwitcher.js").then((m) => ({ default: m.DashboardSwitcher })));
const Inputs = lazy(() => import("./pages/Inputs.js").then((m) => ({ default: m.Inputs })));
const SignalDetail = lazy(() => import("./pages/Inputs.js").then((m) => ({ default: m.SignalDetail })));
const Graph = lazy(() => import("./pages/Graph.js").then((m) => ({ default: m.Graph })));
const DevicePage = lazy(() => import("./pages/Devices.js").then((m) => ({ default: m.DevicePage })));
const RigPage = lazy(() => import("./pages/Rig.js").then((m) => ({ default: m.RigPage })));
const Controllers = lazy(() => import("./pages/Controllers.js").then((m) => ({ default: m.Controllers })));
const Events = lazy(() => import("./pages/Events.js").then((m) => ({ default: m.Events })));
const Sessions = lazy(() => import("./pages/Sessions.js").then((m) => ({ default: m.Sessions })));
const Programs = lazy(() => import("./pages/Programs.js").then((m) => ({ default: m.Programs })));
const ProgramDetail = lazy(() => import("./pages/Programs.js").then((m) => ({ default: m.ProgramDetail })));
const Simulation = lazy(() => import("./pages/Simulation.js").then((m) => ({ default: m.Simulation })));

const SimulationPage = memo(Simulation);

// The chart window default (see `windowS` in `App`): a placeholder until the store has enough
// to fit to, then the floor and ceiling of that fit.
const DEFAULT_WINDOW_S = 300;
const MIN_WINDOW_S = 60;
const MAX_WINDOW_S = 3600;

/**
 * Settles the default chart window once telemetry has both ends of what it holds, then goes
 * away: `useNowS()` re-renders its host on every tick, so keeping it mounted in `App` itself
 * re-rendered the whole app forever after the one-time settle it exists for. Isolated here and
 * unmounted by the parent once `onSettle` fires, it takes the ticking with it.
 */
function WindowSettler({ telemetry, onSettle }: { telemetry: ReturnType<typeof useTelemetry>; onSettle(windowS: number): void }) {
  countRender("WindowSettler");
  const tickS = useNowS();
  useEffect(() => {
    const now = telemetry.nowS();
    const earliest = telemetry.earliestS();
    if (now === null || earliest === null) return;
    onSettle(Math.min(Math.max(now - earliest, MIN_WINDOW_S), MAX_WINDOW_S));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickS]);
  return null;
}

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

/**
 * The app bar's chips: the polled state plus the store's stream health. The
 * paused chip shows only away from the Simulation page, where the transport
 * itself is: pausing there freezes every page's samples, and a page with no
 * transport in sight still needs a way back to live.
 */
function AppStatus({ onSignIn, playback, page }: { onSignIn(): void; playback: PlaybackHook; page: Page }) {
  const { recording, programmer, simulationSpeed } = useLive();
  const simulated = useContext(SimulatedContext);
  const { streams, byStream } = useStreamStatus();
  return (
    <>
      <Status recording={recording} programmer={programmer} streams={streams} byStream={byStream} />
      {simulated && <SimChip speed={simulationSpeed} />}
      {simulated && page !== "simulation" && <PausedChip playback={playback} />}
      {/* Self-contained: renders nothing without OPERATE, so it costs nothing to mount everywhere.
          Reachable from every page by living in the app bar, ahead of the UI split (brain/plans/ui-split.md)
          that will move this row into a redesigned shell. */}
      <StopButton />
      <AuthChip onSignIn={onSignIn} />
    </>
  );
}

/** Shown for the moment a page's own chunk is still downloading; same wording as the devices load. */
function PageFallback() {
  return (
    <Typography color="text.secondary" sx={{ m: 3 }}>
      loading…
    </Typography>
  );
}

/**
 * `<Shell>`'s app bar (status chips, `StopButton`) and drawer live above this, as a sibling of
 * `children`, not a descendant -- but React 18 unmounts the whole root on an uncaught render
 * error regardless of that layout, unless something between the failing component and the root
 * catches it first. This is that boundary: a page that throws is replaced in place, and the app
 * bar (with the stop button) stays mounted and usable. Keyed on `page` so navigating away from a
 * crashed page (the nav and app bar are unaffected by the crash, so that navigation still works)
 * gives the next page a fresh mount rather than reusing the caught error state.
 */
class PageBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("page failed", error, info.componentStack);
  }
  override render() {
    if (!this.state.error) return this.props.children;
    return (
      <Alert severity="error" sx={{ m: 3 }}>
        This page failed to render ({this.state.error.message}). Pick another page from the sidebar, or reload.
      </Alert>
    );
  }
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

function EventsPage({ level, unread, onMarkRead, onMarkAllRead }: { level: string | undefined; unread: ReadonlySet<string>; onMarkRead(e: RigEvent): void; onMarkAllRead(): void }) {
  const { events, error } = useEvents(500);
  return <Events events={events} error={error} level={level} unread={unread} onMarkRead={onMarkRead} onMarkAllRead={onMarkAllRead} />;
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
export function App({ onSignIn }: { onSignIn(): void }) {
  countRender("App");
  const authInfo = useAuth().info;
  // Open to the network, or moved to loopback for want of a password: said on every page, never dismissed.
  const exposure = authInfo?.exposure;
  const [route, navigate] = useRoute();
  const { page, name, params } = route;
  // A bare `#/` opens the home dashboard when one is set; the Overview stays a click away.
  useEffect(() => {
    if (/^#?\/?$/.test(window.location.hash) && readHome()) window.location.replace(hashFor("dashboards"));
  }, []);
  // A fixed 5 min undershoots a rig with hours of history (`longrun`); a fixed 1 h is just as
  // wrong the other way for one that started a minute ago. So the default fits whatever has
  // actually loaded, capped at an hour -- settled once, the first time both ends of that are
  // known, rather than tracking the growing extent forever (which would keep widening the
  // window under a running chart the operator hasn't touched). An explicit choice (`onWindow`)
  // always overrides this; a widget's own saved `window_s` never reaches this default at all.
  const [windowS, setWindowS] = useState(DEFAULT_WINDOW_S);
  const [windowSettled, setWindowSettled] = useState(false);
  const telemetry = useTelemetry();
  const onSettleWindow = useCallback((w: number) => {
    setWindowS(w);
    setWindowSettled(true);
  }, []);
  const [yScale, setYScaleState] = useState<YScale>(readYScale);
  const setYScale = useCallback((s: YScale) => {
    setYScaleState(s);
    writeYScale(s);
  }, []);
  const devices = useDevices();
  const ready = Boolean(devices.data);
  useScrollMemory(ready);
  // One canonical read/unread + toast-queue instance, shared by the nav badge, the toast
  // stack and the Events page itself, so they never disagree about what's unread.
  const { events: liveEvents } = useEvents(500);
  const unreadEvents = useUnreadEvents(liveEvents);
  // Paused, the store serves the page the past instead of the live rings: as much of it as the widest chart shows.
  // The transport lives on the Simulation page; the app bar's paused chip is the way back from any other.
  const playback = usePlayback({ windowS });
  // Kept mounted across every branch below (error, loading, the full page) until it settles
  // once; after that it is gone from the tree for the rest of App's life.
  const settler = windowSettled ? null : <WindowSettler telemetry={telemetry} onSettle={onSettleWindow} />;
  // `flyball run` now starts the front before the runner, so a page opened straight away sees a
  // 503 "The rig's runner is starting" (Retry-After: 1) rather than any real failure: retry on a
  // backoff instead of showing the permanent "cannot reach" alert until someone reloads.
  const starting = devices.error instanceof RigError && devices.error.status === 503;
  useStartingRetry(starting, devices.refresh);

  if (devices.error) {
    // The session ended (or a runner refuses everything and the door has not yet said so): the first
    // request the app makes is what discovers it, so the login page stands in for the usual error.
    if (devices.error instanceof RigError && devices.error.status === 401)
      return (
        <>
          {settler}
          <LoginPage />
        </>
      );
    if (starting)
      return (
        <>
          {settler}
          <Typography color="text.secondary" sx={{ m: 3 }}>
            starting…
          </Typography>
        </>
      );
    // A signed-in caller with no `read` verb (a proxy identity mapped to nothing, say): the rig is
    // reachable, this caller just is not allowed to see it. "Cannot reach the rig" would be a lie.
    if (devices.error instanceof RigError && devices.error.status === 403 && authInfo?.user)
      return (
        <>
          {settler}
          <Alert severity="warning" sx={{ m: 3 }}>
            Signed in as {authInfo.user.name}, but without permission to view this rig.
          </Alert>
        </>
      );
    return (
      <>
        {settler}
        <Alert severity="error" sx={{ m: 3 }}>
          Cannot reach the rig: {devices.error.message}
        </Alert>
      </>
    );
  }
  if (!devices.data)
    return (
      <>
        {settler}
        <Typography color="text.secondary" sx={{ m: 3 }}>
          loading…
        </Typography>
      </>
    );

  const all = devices.data;
  // A detail page is titled by the thing's label, as everywhere else on the page: the address stays in the crumbs and hints.
  const titled = (): string => {
    if (name === null) return PAGE_LABEL[page];
    if (page === "sessions") return `Session #${name}`;
    if (page === "devices") return deviceTitle(all.find((d) => d.name === name) ?? { name });
    if (page === "inputs" || page === "controllers") {
      const signal = all.flatMap((d) => signalsOf(d.signals)).find((s) => s.address === name);
      return signal ? signalTitle(signal, all) : name;
    }
    return name;
  };
  const title = titled();
  const charts = { windowS, onWindow: setWindowS, yScale, onYScale: setYScale };
  const openDashboard = (n: string | null, generated?: boolean) => (window.location.hash = hashFor("dashboards", n, generated ? { generated: "" } : {}));

  return (
    <>
      {settler}
      <LinksProvider hrefFor={hrefFor}>
        <LiveProvider>
          <SimulatedContext.Consumer>
            {(simulated) => (
              <Shell
                page={page}
                onNavigate={navigate}
                title={title}
                status={<AppStatus onSignIn={onSignIn} playback={playback} page={page} />}
                simulated={simulated}
                devices={all.filter((d) => d.kind !== "simulation")}
                current={name}
                eventsUnread={unreadEvents.unreadCount}
                startSlot={
                  page === "dashboards" ? (
                    <Suspense fallback={null}>
                      <DashboardSwitcher name={name} generated={"generated" in params} onOpen={openDashboard} />
                    </Suspense>
                  ) : undefined
                }
              >
                <PageBoundary key={page}>
                  <ExposureBanner exposure={exposure} />
                  <Waits />
                  <Suspense fallback={<PageFallback />}>
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
                    {page === "events" && (
                      <EventsPage
                        level={params.level}
                        unread={unreadEvents.unread}
                        onMarkRead={unreadEvents.markRead}
                        onMarkAllRead={unreadEvents.markAllRead}
                      />
                    )}
                    {page === "sessions" && <SessionsPage name={name} navigate={navigate} />}
                    {page === "simulation" && <SimulationPage devices={all} playback={playback} />}
                  </Suspense>
                </PageBoundary>
              </Shell>
            )}
          </SimulatedContext.Consumer>
        </LiveProvider>
        <EventToasts toasts={unreadEvents.toasts} onDismiss={unreadEvents.dismissToast} />
      </LinksProvider>
    </>
  );
}
