/**
 * The shell.
 *
 * Owns the settings, the one rig connection and which tab is showing; every
 * page is a pure function of those. Nothing below this file opens a socket.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { StatusBar } from "./components/StatusBar";
import { CommandFeedback } from "./components/CommandFeedback";
import { Control } from "./pages/Control";
import { Dashboard } from "./pages/Dashboard";
import { Programs } from "./pages/Programs";
import { Settings } from "./pages/Settings";
import { loadSettings, saveSettings, type Settings as SettingsValue } from "./state/settings";
import { usePrograms } from "./state/usePrograms";
import { useRig } from "./state/useRig";

type Tab = "dashboard" | "control" | "programs" | "settings";

const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "control", label: "Control" },
  { id: "programs", label: "Programs" },
  { id: "settings", label: "Settings" },
];

export function App() {
  const [settings, setSettings] = useState<SettingsValue>(loadSettings);
  const [tab, setTab] = useState<Tab>("dashboard");

  useEffect(() => saveSettings(settings), [settings]);

  useEffect(() => {
    const root = document.documentElement;
    if (settings.theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", settings.theme);
  }, [settings.theme]);

  const change = useCallback(
    (partial: Partial<SettingsValue>) => setSettings((current) => ({ ...current, ...partial })),
    [],
  );

  const rig = useRig(settings);
  const library = usePrograms();
  const state = rig.state;

  const page = useMemo(() => {
    switch (tab) {
      case "dashboard":
        return <Dashboard rig={rig} state={state} defaultWindow={settings.windowSeconds} />;
      case "control":
        return <Control rig={rig} state={state} confirm={settings.confirmActions} />;
      case "programs":
        return (
          <Programs rig={rig} state={state} library={library} confirm={settings.confirmActions} />
        );
      case "settings":
        return <Settings settings={settings} onChange={change} rig={rig} />;
    }
  }, [tab, rig, state, settings, library, change]);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">humctrl</span>
        <nav className="tabs" role="tablist">
          {TABS.map((each) => (
            <button
              key={each.id}
              type="button"
              role="tab"
              className="tab"
              aria-selected={tab === each.id}
              onClick={() => setTab(each.id)}
            >
              {each.label}
            </button>
          ))}
        </nav>
        <span className="spacer" />
        <StatusBar
          status={rig.status}
          state={state}
          capabilities={rig.capabilities}
          source={settings.source}
          label={rig.transport.label}
        />
      </header>

      <main className="page stack">
        <CommandFeedback command={rig.command} onDismiss={rig.clearError} />
        {page}
      </main>
    </div>
  );
}
