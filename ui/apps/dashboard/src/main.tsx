import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import { Button, Snackbar } from "@mui/material";
import { RigProvider } from "@flyball/react";
import { browserTransport } from "@flyball/client";
import "uplot/dist/uPlot.min.css";
import "react-grid-layout/css/styles.css";
import "@flyball/react/styles.css";
import "./app.css";
import { AppTheme } from "./theme.js";
import { AuthProvider, useAuth, watchUnauthorized } from "./auth.js";
import { LoginPage } from "./Login.js";
import { App } from "./App.js";

/**
 * The door, then the app. While the daemon says this browser may see nothing, the login page is all there
 * is; a sign in or out rebuilds `<RigProvider>` (a new transport), which reconnects every socket at once.
 * A reader on a daemon anyone may look at gets the app, and a nudge to sign in when a write is refused.
 */
function Root() {
  const auth = useAuth();
  const { epoch, unauthorized, mustSignIn, canOperate, denied, info, error } = auth;
  // The transport tells the door about every 401; `epoch` in the deps is what rebuilds it after a sign in.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const transport = useMemo(() => watchUnauthorized(browserTransport(), unauthorized), [unauthorized, epoch]);
  const [wantLogin, setWantLogin] = useState(false);
  const [nudge, setNudge] = useState(false);
  useEffect(() => {
    if (denied && !canOperate) setNudge(true);
  }, [denied, canOperate]);
  useEffect(() => {
    if (canOperate) {
      setWantLogin(false);
      setNudge(false);
    }
  }, [canOperate]);

  // Nothing until the daemon has said which door it has: an app mounted before that would knock on every
  // route and be refused. Unreachable is different: the app shows its own "cannot reach the rig".
  if (info === null && !error) return null;
  if (mustSignIn) return <LoginPage />;
  if (wantLogin) return <LoginPage onCancel={() => setWantLogin(false)} />;
  return (
    <RigProvider transport={transport}>
      <App onSignIn={() => setWantLogin(true)} />
      <Snackbar
        open={nudge}
        autoHideDuration={6000}
        onClose={() => setNudge(false)}
        message="Sign in to operate this rig"
        action={
          <Button size="small" color="inherit" onClick={() => setWantLogin(true)} data-testid="nudge-signin">
            Sign in
          </Button>
        }
        data-testid="auth-nudge"
      />
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
      <AuthProvider>
        <Root />
      </AuthProvider>
    </AppTheme>
  </React.StrictMode>,
);
