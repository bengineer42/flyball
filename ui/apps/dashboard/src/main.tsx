import React from "react";
import ReactDOM from "react-dom/client";
import { RigProvider } from "@flyball/react";
import "uplot/dist/uPlot.min.css";
import "react-grid-layout/css/styles.css";
import "@flyball/react/styles.css";
import "./app.css";
import { AppTheme } from "./theme.js";
import { TokenProvider, useToken } from "./token.js";
import { App } from "./App.js";

/** Reads the token from `<TokenProvider>` above and hands it to `<RigProvider>`: a change here rebuilds the
 * client and every socket, which is how entering a token after a 401 reconnects everything at once. */
function Root() {
  const { token } = useToken();
  return (
    <RigProvider token={token || undefined}>
      <App />
    </RigProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppTheme>
      <TokenProvider>
        <Root />
      </TokenProvider>
    </AppTheme>
  </React.StrictMode>,
);
