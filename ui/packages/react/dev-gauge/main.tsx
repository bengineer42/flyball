import React from "react";
import ReactDOM from "react-dom/client";
import type { ChannelOut } from "@flyball/client";
import { Gauge, Readout, type GaugeKind } from "@flyball/react";
import "@flyball/react/styles.css";

const thermo: ChannelOut = {
  source: "thermocouple", measurand: "temperature", unit: "°C", label: "Temperature",
  range: [0, 120], precision: 2, warn: [30, 90], alarm: [10, 110],
};
const rh: ChannelOut = {
  source: "probe", measurand: "humidity", unit: "%RH", label: "Humidity",
  range: [0, 100], precision: 1, warn: [40, 60], alarm: [20, 80],
};
const pressure: ChannelOut = {
  source: "gauge", measurand: "pressure", unit: "bar", label: "Pressure",
  range: [0, 10], precision: 2, warn: [2, 8], alarm: null,
};
const plain: ChannelOut = {
  source: "meter", measurand: "flow", unit: "L/min", label: "Flow", range: [0, 50], precision: 1,
};

const kinds: GaugeKind[] = ["thermometer", "tank", "dial", "bar"];
const values = { "below alarm": 5, "in warn": 20, ok: 60, "above alarm": 115, none: undefined } as const;

function Row({ kind }: { kind: GaugeKind }) {
  return (
    <div style={{ display: "flex", gap: "1.5rem", alignItems: "flex-end", padding: "0.5rem 0", borderBottom: "1px solid #ddd" }}>
      <div style={{ width: "7rem" }}>{kind}</div>
      {Object.entries(values).map(([name, v]) => (
        <div key={name} style={{ width: kind === "bar" ? "9rem" : undefined, textAlign: "center" }}>
          <Gauge channel={thermo} value={v} kind={kind} />
          <div style={{ fontSize: "0.75em", color: "#666" }}>{name}</div>
        </div>
      ))}
      <div style={{ width: kind === "bar" ? "9rem" : undefined, textAlign: "center" }}>
        <Gauge channel={kind === "tank" ? rh : kind === "dial" ? pressure : plain} value={kind === "dial" ? 1.2 : 45} kind={kind} />
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
          <Readout key={name} channel={thermo} t={t} v={v === undefined ? [] : [v, v, v, v, v]} sparkline={false} />
        ))}
        <Readout channel={plain} t={t} v={[45, 45, 45, 45, 45]} sparkline={false} />
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
