import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import { RigProvider, useLoops, useEvents, LoopPanel, EventsPanel } from "@flyball/react";
import "uplot/dist/uPlot.min.css";
import "@flyball/react/styles.css";

function Harness() {
  const [windowS, setWindowS] = useState(120);
  const { loops, history, status } = useLoops(3600);
  const events = useEvents(500);
  const select = (
    <select value={windowS} onChange={(e) => setWindowS(Number(e.target.value))}>
      {[60, 120, 600, 3600].map((s) => (
        <option key={s} value={s}>{s} s</option>
      ))}
    </select>
  );
  return (
    <>
      <div id="status" data-loops={status} data-events={events.status}>
        loops: {status} · events: {events.status}
      </div>
      {Object.values(loops).map((loop) => (
        <LoopPanel
          key={loop.name}
          loop={loop}
          history={history[loop.name]}
          windowS={windowS}
          controls={select}
        />
      ))}
      <EventsPanel events={events.events} onSelect={(e) => console.log("select", e.kind)} />
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RigProvider>
      <Harness />
    </RigProvider>
  </React.StrictMode>,
);
