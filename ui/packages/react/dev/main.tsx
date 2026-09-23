import React, { useState } from "react";
import ReactDOM from "react-dom/client";
import { signalsOf, type SignalOut } from "@flyball/client";
import { RigProvider, useControllers, useDevices, useEvents, useNowS, useActivities, ControllerPanel, DeviceSignals, EventsPanel, PromptPanel } from "@flyball/react";
import "uplot/dist/uPlot.min.css";
import "@flyball/react/styles.css";

function Harness() {
  const [windowS, setWindowS] = useState(120);
  const { controllers, history, status } = useControllers(3600);
  const devices = useDevices(10000);
  const events = useEvents(500);
  const activities = useActivities();
  const nowS = useNowS();
  const select = (
    <select value={windowS} onChange={(e) => setWindowS(Number(e.target.value))}>
      {[60, 120, 600, 3600].map((s) => (
        <option key={s} value={s}>{s} s</option>
      ))}
    </select>
  );
  // Every signal of every device by address, for a controller's measured signal and output.
  const signals = new Map<string, SignalOut>();
  for (const device of devices.data ?? []) for (const s of signalsOf(device.signals)) signals.set(s.address, s);
  return (
    <>
      <div id="status" data-controllers={status} data-events={events.status} data-devices={devices.loading ? "loading" : devices.error ? "error" : "ok"}>
        controllers: {status} · events: {events.status} · devices: {devices.data?.length ?? (devices.error?.message ?? "loading")}
      </div>
      <PromptPanel prompts={activities.pending} nowS={nowS} onFire={activities.fire} onCancel={activities.cancel} />
      {Object.values(controllers).map((controller) => {
        const source = signals.get(controller.measured_signal);
        return source ? (
          <ControllerPanel key={controller.name} controller={controller} source={source} target={signals.get(controller.output_signal)} history={history[controller.name]} windowS={windowS} controls={select} detail />
        ) : null;
      })}
      {(devices.data ?? []).filter((d) => d.kind === "device").map((device) => (
        <DeviceSignals key={device.name} device={device} windowS={windowS} />
      ))}
      <EventsPanel events={events.events} nowS={nowS} onSelect={(e) => console.log("select", e.kind)} />
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
