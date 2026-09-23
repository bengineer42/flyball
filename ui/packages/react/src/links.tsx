/**
 * How a panel links a name to a page. The library never knows the app's
 * routes: the app supplies `hrefFor`, panels render `<Ref>` and get an anchor
 * when the app has a page for that thing, plain text when it does not.
 */

import { createContext, useContext, type ReactNode } from "react";

/** What a name can refer to: a device by name, a signal by address, a controller by its name (its output's address). */
export type RefKind = "device" | "signal" | "controller" | "session" | "event";

export interface Ref {
  kind: RefKind;
  /** The device's name, the signal's address, the controller's name, the session's id. */
  name: string;
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
export function Ref({ kind, name, children, className }: Ref & { children?: ReactNode; className?: string }) {
  const href = useHref({ kind, name });
  const label = children ?? name;
  const cls = ["fb-ref", className].filter(Boolean).join(" ");
  return href ? (
    <a className={cls} href={href} title={`${kind} ${name}`}>
      {label}
    </a>
  ) : (
    <span className={cls}>{label}</span>
  );
}
