import { useState } from "react";
import { Alert, Typography } from "@mui/material";
import { LinksProvider, useRigSchema, useActuatorStates, useSources, useSamples, useRecording, useEvents } from "@flyball/react";
import { Actuator } from "./Actuator.js";
import { Shell } from "./Shell.js";
import { PAGES, hashFor, hrefFor, useRoute, type Page } from "./router.js";
import { Status, StreamChip } from "./Status.js";
import { Overview } from "./pages/Overview.js";
import { Sources, SourceDetail, ChannelDetail, Crumbs } from "./pages/Sources.js";
import { ActuatorDetail, ReaderDetail, Readers } from "./pages/Devices.js";
import { Loops } from "./pages/Loops.js";
import { Events } from "./pages/Events.js";
import { Sessions } from "./pages/Sessions.js";

const PAGE_LABEL: Record<Page, string> = Object.fromEntries(PAGES.map((p) => [p.id, p.label])) as Record<Page, string>;
PAGE_LABEL.readers = "Readers";

/** The app: one rig document, the live streams, and a page per section. Nothing here knows what the rig is. */
export function App() {
  const [{ page, name, measurand }, navigate] = useRoute();
  const [windowS, setWindowS] = useState(300);
  const schema = useRigSchema();
  const sources = useSources();
  const recording = useRecording(5000);
  const events = useEvents(500);
  const { states, status: actuatorStream } = useActuatorStates();
  // Hold an hour so a wider window has history to show; charts trim to the selected window.
  const { traces, status: sampleStream } = useSamples(sources.data, 3600);

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
      <Status actuators={actuators} states={states} recording={recording} />
    </>
  );
  const title = name === null ? PAGE_LABEL[page] : measurand ? `${name}.${measurand}` : page === "sessions" ? `Session #${name}` : name;
  const source = name === null ? undefined : sources.data.find((s) => s.name === name);

  return (
    <LinksProvider hrefFor={hrefFor}>
      <Shell page={page === "readers" ? "overview" : page} onNavigate={navigate} title={title} status={status}>
        {page === "overview" && (
          <Overview
            schema={rig}
            sources={sources.data}
            traces={traces}
            states={states}
            events={events.events}
            onOpen={navigate}
            windowS={windowS}
            onWindow={setWindowS}
          />
        )}
        {page === "sources" && name === null && <Sources sources={sources.data} traces={traces} windowS={windowS} onWindow={setWindowS} />}
        {page === "sources" && name !== null && measurand === null && (
          <SourceDetail source={source} traces={traces} windowS={windowS} onWindow={setWindowS} />
        )}
        {page === "sources" && name !== null && measurand !== null && (
          <ChannelDetail source={source} measurand={measurand} traces={traces} windowS={windowS} onWindow={setWindowS} />
        )}
        {page === "actuators" && name === null && (
          <div className="tiles tiles-full">
            {actuators.map((a) => (
              <Actuator key={a.name} schema={a} state={states[a.name]} />
            ))}
            {actuators.length === 0 && <Typography color="text.secondary">no actuators attached</Typography>}
          </div>
        )}
        {page === "actuators" && name !== null && <ActuatorDetail schema={rig} name={name} states={states} />}
        {page === "readers" && name === null && <Readers schema={rig} />}
        {page === "readers" && name !== null && <ReaderDetail schema={rig} name={name} />}
        {page === "loops" && (
          <>
            {name !== null && <Crumbs items={[{ label: "loops", href: hashFor("loops") }, { label: name }]} />}
            <Loops windowS={windowS} onWindow={setWindowS} name={name} />
          </>
        )}
        {page === "events" && <Events events={events.events} error={events.error} />}
        {page === "sessions" && (
          <Sessions recording={recording} selected={name !== null && /^\d+$/.test(name) ? Number(name) : null} onSelect={(i) => navigate("sessions", i)} />
        )}
      </Shell>
    </LinksProvider>
  );
}
