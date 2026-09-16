import { memo, useCallback, useEffect, useState } from "react";
import { Alert, Typography } from "@mui/material";
import { LinksProvider, SignalPrompt, useSignals, useRigSchema, useActuatorStates, useSources, useSamples, useRecording, useEvents, useQuery, useRig, useSimulation, type YScale } from "@flyball/react";
import { Actuator } from "./Actuator.js";
import { Shell } from "./Shell.js";
import { PAGES, hashFor, hrefFor, useRoute, useScrollMemory, type Page } from "./router.js";
import { Status, StreamChip, SimChip } from "./Status.js";
import { Overview } from "./pages/Overview.js";
import { Dashboards } from "./pages/Dashboards.js";
import { Sources, SourceDetail, ChannelDetail } from "./pages/Sources.js";
import { ActuatorDetail, ReaderDetail, Readers } from "./pages/Devices.js";
import { Loops } from "./pages/Loops.js";
import { Events } from "./pages/Events.js";
import { Sessions } from "./pages/Sessions.js";
import { Programs, ProgramDetail } from "./pages/Programs.js";
import { Simulation } from "./pages/Simulation.js";
import { readYScale, writeYScale, readEvery, writeEvery } from "./YScaleSelect.js";
import { readHome } from "./dashboard/home.js";

// The app re-renders on every sample; the Simulation page has nothing to do with samples and holds form controls.
const SimulationPage = memo(Simulation);

const PAGE_LABEL: Record<Page, string> = Object.fromEntries(PAGES.map((p) => [p.id, p.label])) as Record<Page, string>;
PAGE_LABEL.readers = "Readers";

/** The app: one rig document, the live streams, and a page per section. Nothing here knows what the rig is. */
export function App() {
  const [{ page, name, measurand, params }, navigate] = useRoute();
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
  const schema = useRigSchema();
  const sources = useSources();
  const recording = useRecording(5000);
  const events = useEvents(500);
  const rigClient = useRig();
  const simulation = useSimulation();
  // A program waiting on a person shows on every page: nobody should have to go looking for the Go button.
  const signals = useSignals();
  const [programRunning, setProgramRunning] = useState(false);
  // Poll the programmer every second while a program runs, every five otherwise.
  const programmer = useQuery(async () => {
    const p = await rigClient.programmer();
    setProgramRunning(p.running);
    return p;
  }, [rigClient], { refreshMs: programRunning ? 1000 : 5000 });
  const { states, status: actuatorStream } = useActuatorStates();
  // Hold an hour so a wider window has history to show; charts trim to the selected window.
  const { traces, status: sampleStream } = useSamples(sources.data, 3600);
  const ready = Boolean(schema.data && sources.data);
  useScrollMemory(ready);

  if (schema.error)
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        Cannot reach the rig: {schema.error.message}
      </Alert>
    );
  if (!schema.data || !sources.data)
    return (
      <Typography color="text.secondary" sx={{ m: 2 }}>
        loading…
      </Typography>
    );

  const rig = schema.data;
  const actuators = Object.values(rig.actuators);
  const status = (
    <>
      <StreamChip status={sampleStream} label="samples" />
      <StreamChip status={actuatorStream} label="actuators" />
      <Status actuators={actuators} states={states} recording={recording} programmer={programmer} />
      {simulation.attached && <SimChip speed={simulation.speed} />}
    </>
  );
  const title = name === null ? PAGE_LABEL[page] : measurand ? `${name}.${measurand}` : page === "sessions" ? `Session #${name}` : name;
  const source = name === null ? undefined : sources.data.find((s) => s.name === name);
  const charts = { windowS, onWindow: setWindowS, yScale, onYScale: setYScale, every, onEvery: setEvery };

  return (
    <LinksProvider hrefFor={hrefFor}>
      <Shell page={page === "readers" ? "overview" : page} onNavigate={navigate} title={title} status={status} simulated={simulation.attached}>
        <SignalPrompt signals={signals.pending} onFire={signals.fire} onInterrupt={signals.interrupt} />
        {page === "overview" && (
          <Overview schema={rig} sources={sources.data} traces={traces} states={states} events={events.events} onOpen={navigate} {...charts} />
        )}
        {page === "dashboards" && (
          <Dashboards
            name={name}
            generated={"generated" in params}
            schema={rig}
            sources={sources.data}
            traces={traces}
            states={states}
            events={events.events}
            recording={recording}
            programmer={programmer}
            onOpen={(n, generated) => (window.location.hash = hashFor("dashboards", n, null, generated ? { generated: "" } : {}))}
            {...charts}
          />
        )}
        {page === "sources" && name === null && <Sources sources={sources.data} traces={traces} {...charts} />}
        {page === "sources" && name !== null && measurand === null && <SourceDetail source={source} traces={traces} {...charts} />}
        {page === "sources" && name !== null && measurand !== null && (
          <ChannelDetail source={source} measurand={measurand} traces={traces} {...charts} />
        )}
        {page === "actuators" && name === null && (
          <div className="grid">
            {actuators.map((a) => (
              <div key={a.name} className="c6 xl4">
                <Actuator schema={a} state={states[a.name]} />
              </div>
            ))}
            {actuators.length === 0 && <Typography color="text.secondary">no actuators attached</Typography>}
          </div>
        )}
        {page === "actuators" && name !== null && <ActuatorDetail schema={rig} name={name} states={states} />}
        {page === "readers" && name === null && <Readers schema={rig} />}
        {page === "readers" && name !== null && <ReaderDetail schema={rig} name={name} />}
        {page === "loops" && <Loops {...charts} name={name} />}
        {page === "programs" && name === null && <Programs programmer={programmer} events={events.events} onOpen={(n) => navigate("programs", n)} />}
        {page === "programs" && name !== null && (
          <ProgramDetail
            key={name}
            name={name}
            programmer={programmer}
            events={events.events}
            onSaved={(n) => navigate("programs", n)}
            onDeleted={() => navigate("programs")}
          />
        )}
        {page === "events" && <Events events={events.events} error={events.error} level={params.level} />}
        {page === "sessions" && (
          <Sessions recording={recording} selected={name !== null && /^\d+$/.test(name) ? Number(name) : null} onSelect={(i) => navigate("sessions", i)} />
        )}
        {page === "simulation" && <SimulationPage />}
      </Shell>
    </LinksProvider>
  );
}
