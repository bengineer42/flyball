/**
 * How a panel links a name to a page. The library never knows the app's
 * routes: the app supplies `hrefFor`, panels render `<Ref>` and get an anchor
 * when the app has a page for that thing, plain text when it does not.
 */

import { createContext, useContext, type ReactNode } from "react";

export type RefKind = "source" | "channel" | "actuator" | "reader" | "loop" | "session" | "event";

export interface Ref {
  kind: RefKind;
  name: string;
  /** For `channel`: the measurand; for `event`: unused. */
  measurand?: string;
}

export type HrefFor = (ref: Ref) => string | undefined;

const LinksContext = createContext<HrefFor>(() => undefined);

/** Supply the app's routes to every panel below. */
export function LinksProvider({ hrefFor, children }: { hrefFor: HrefFor; children: ReactNode }) {
  return <LinksContext.Provider value={hrefFor}>{children}</LinksContext.Provider>;
}

export function useHref(ref: Ref): string | undefined {
  return useContext(LinksContext)(ref);
}

/** A name that is a link when the app has somewhere to go, and text otherwise. */
export function Ref({ kind, name, measurand, children, className }: Ref & { children?: ReactNode; className?: string }) {
  const href = useHref({ kind, name, ...(measurand !== undefined ? { measurand } : {}) });
  const label = children ?? (measurand !== undefined ? `${name}.${measurand}` : name);
  const cls = ["fb-ref", className].filter(Boolean).join(" ");
  return href ? (
    <a className={cls} href={href} title={measurand !== undefined ? `${kind} ${name}.${measurand}` : `${kind} ${name}`}>
      {label}
    </a>
  ) : (
    <span className={cls}>{label}</span>
  );
}
