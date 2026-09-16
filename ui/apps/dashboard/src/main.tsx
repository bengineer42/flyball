import React from "react";
import ReactDOM from "react-dom/client";
import { RigProvider } from "@flyball/react";
import "uplot/dist/uPlot.min.css";
import "react-grid-layout/css/styles.css";
import "@flyball/react/styles.css";
import "./app.css";
import { AppTheme } from "./theme.js";
import { App } from "./App.js";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppTheme>
      <RigProvider>
        <App />
      </RigProvider>
    </AppTheme>
  </React.StrictMode>,
);
