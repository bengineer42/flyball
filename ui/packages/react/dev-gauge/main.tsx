import React from "react";
import ReactDOM from "react-dom/client";
import type { SignalOut } from "@flyball/client";
import { Gauge, Readout, type GaugeKind } from "@flyball/react";
import "@flyball/react/styles.css";

/** A publishing signal with the given bands, as `GET /api/devices` would list it. */
function signal(address: string, label: string, quantity: string, unit: string, bands: Pick<SignalOut, "range" | "precision" | "warn" | "alarm">): SignalOut {
  return {
    name: address.slice(address.indexOf(".") + 1), address, access: "rp", label, quantity, unit, dimension: null, dtype: "float64", shape: [],
    poll_s: null, limits: null, together: [], latest: null, write: null, ...bands,
  };
}
const thermo = signal("thermocouple.temperature", "Temperature", "temperature", "°C", { range: [0, 120], precision: 2, warn: [30, 90], alarm: [10, 110] });
const rh = signal("probe.humidity", "Humidity", "humidity", "%RH", { range: [0, 100], precision: 1, warn: [40, 60], alarm: [20, 80] });
const pressure = signal("gauge.pressure", "Pressure", "pressure", "bar", { range: [0, 10], precision: 2, warn: [2, 8], alarm: null });
const plain = signal("meter.flow", "Flow", "flow", "L/min", { range: [0, 50], precision: 1, warn: null, alarm: null });

const kinds: GaugeKind[] = ["thermometer", "tank", "dial", "bar"];
const values = { "below alarm": 5, "in warn": 20, ok: 60, "above alarm": 115, none: undefined } as const;

function Row({ kind }: { kind: GaugeKind }) {
  return (
    <div style={{ display: "flex", gap: "1.5rem", alignItems: "flex-end", padding: "0.5rem 0", borderBottom: "1px solid #ddd" }}>
      <div style={{ width: "7rem" }}>{kind}</div>
      {Object.entries(values).map(([name, v]) => (
        <div key={name} style={{ width: kind === "bar" ? "9rem" : undefined, textAlign: "center" }}>
          <Gauge signal={thermo} value={v} kind={kind} />
          <div style={{ fontSize: "0.75em", color: "#666" }}>{name}</div>
        </div>
      ))}
      <div style={{ width: kind === "bar" ? "9rem" : undefined, textAlign: "center" }}>
        <Gauge signal={kind === "tank" ? rh : kind === "dial" ? pressure : plain} value={kind === "dial" ? 1.2 : 45} kind={kind} />
        <div style={{ fontSize: "0.75em", color: "#666" }}>{kind === "tank" ? "%RH" : kind === "dial" ? "warn only" : "no bands"}</div>
      </div>
    </div>
  );
}

function App() {
  const t = [0, 1, 2, 3, 4];
  return (
    <div style={{ fontFamily: "system-ui, sans-serif", padding: "1rem" }}>
      <h3 style={{ margin: "0 0 0.5rem" }}>Gauge</h3>
      {kinds.map((k) => <Row key={k} kind={k} />)}
      <h3 style={{ margin: "1rem 0 0.5rem" }}>Readout</h3>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 11rem)", gap: "0.75rem" }}>
        {Object.entries(values).map(([name, v]) => (
          <Readout key={name} signal={thermo} t={t} v={v === undefined ? [] : [v, v, v, v, v]} sparkline={false} />
        ))}
        <Readout signal={plain} t={t} v={[45, 45, 45, 45, 45]} sparkline={false} />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
