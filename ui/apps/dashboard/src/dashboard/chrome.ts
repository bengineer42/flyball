/**
 * The one-frame rule (DESIGN-SPEC.md §2, §10): `WidgetFrame` draws the only
 * chrome a widget has -- status dot, title, subtitle, actions -- and the
 * widget draws its body. What the body knows and the frame does not (the
 * signal's severity, a stale footer, a mode chip) travels up through this
 * context: the widget calls `useWidgetChrome({...})` and the frame re-renders
 * its title row with it. Nothing else re-renders: the widget component is
 * memoised on its props and the frame's state lives beside it, not above.
 */
import { createContext, useContext, useEffect } from "react";
import type { ReactNode } from "react";
import type { PanelSeverity } from "@flyball/react";

export interface WidgetChrome {
  /** Replaces the generated title when the document has none of its own (a `Ref` link to the signal, say). */
  title?: ReactNode;
  subtitle?: ReactNode;
  /** Before the actions, always visible: a mode chip or other text status. */
  status?: ReactNode;
  severity?: PanelSeverity;
  severityLabel?: string;
  /** 12px fg-2 line under the body: "last sample 42 s ago". */
  footer?: ReactNode;
}

export type SetWidgetChrome = (chrome: WidgetChrome | null) => void;

export const WidgetChromeContext = createContext<SetWidgetChrome>(() => undefined);

const KEYS: Array<keyof WidgetChrome> = ["title", "subtitle", "status", "severity", "severityLabel", "footer"];

/** Field-wise identity, so a widget that re-renders on every sample only touches the frame when its chrome changes. */
export const sameChrome = (a: WidgetChrome | null, b: WidgetChrome | null) => a === b || (a !== null && b !== null && KEYS.every((k) => Object.is(a[k], b[k])));

/**
 * Hand the frame this widget's title-row content. Pass memoised nodes (or
 * primitives): the frame compares field by field and skips identical chrome.
 */
export function useWidgetChrome(chrome: WidgetChrome | null): void {
  const set = useContext(WidgetChromeContext);
  const deps = KEYS.map((k) => chrome?.[k]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => set(chrome), [set, ...deps]);
  useEffect(() => () => set(null), [set]);
}
