import React from "react";
import ReactDOM from "react-dom/client";
import { RigProvider } from "@flyball/react";
import "@flyball/react/styles.css";
import { AppTheme } from "../../../apps/dashboard/src/theme.js";
import { Simulation } from "../../../apps/dashboard/src/pages/Simulation.js";

// Scratch harness for the Simulation page against a live rig, until the app routes to it.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RigProvider>
      <AppTheme>
        <Simulation />
      </AppTheme>
    </RigProvider>
  </React.StrictMode>,
);
