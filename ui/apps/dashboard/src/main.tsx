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

// One root per page, whatever re-runs this module: Vite re-executes the entry on a hot update that reaches it,
// and a second `createRoot` on the same container throws away the first tree mid-render (`removeChild` errors).
const container = document.getElementById("root")! as HTMLElement & { __fbRoot?: ReactDOM.Root };
const root = (container.__fbRoot ??= ReactDOM.createRoot(container));
root.render(
  <React.StrictMode>
    <AppTheme>
      <TokenProvider>
        <Root />
      </TokenProvider>
    </AppTheme>
  </React.StrictMode>,
);
