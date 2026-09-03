/**
 * Operator settings, kept in the browser.
 *
 * Deliberately not rig configuration: pump limits, control-law gains and loop
 * time belong to the daemon's own config and are read from it. What lives here
 * is how this browser talks to that daemon and how much history it keeps.
 */

import { DEFAULT_MOCK_OPTIONS, type MockOptions } from "../api/mock";

export type Source = "live" | "mock";

export interface Settings {
  source: Source;
  /** Empty means same-origin, which the dev-server proxy handles. */
  apiBase: string;
  /** Samples held for the charts. At 1 Hz, 1800 is half an hour. */
  historyPoints: number;
  /** Seconds of history shown by default. */
  windowSeconds: number;
  theme: "system" | "light" | "dark";
  /** Ask before anything that moves the pumps. */
  confirmActions: boolean;
  mock: MockOptions;
}

export const DEFAULT_SETTINGS: Settings = {
  source: "mock",
  apiBase: "",
  historyPoints: 1800,
  windowSeconds: 300,
  theme: "system",
  confirmActions: false,
  mock: { ...DEFAULT_MOCK_OPTIONS },
};

const KEY = "humctrl.settings.v1";

export function loadSettings(): Settings {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      ...DEFAULT_SETTINGS,
      ...parsed,
      mock: { ...DEFAULT_MOCK_OPTIONS, ...(parsed.mock ?? {}) },
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(settings: Settings): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    /* nothing we can do; the session copy still applies */
  }
}
