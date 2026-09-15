import { useEffect, useState } from "react";
import type { HrefFor } from "@flyball/react";

export type Page = "overview" | "sources" | "actuators" | "loops" | "events" | "sessions" | "readers";
/** The pages in the navigation; `readers` only exists as a detail page. */
export const PAGES: Array<{ id: Exclude<Page, "readers">; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "sources", label: "Sources" },
  { id: "actuators", label: "Actuators" },
  { id: "loops", label: "Loops" },
  { id: "events", label: "Events" },
  { id: "sessions", label: "Sessions" },
];
const ALL_PAGES: Page[] = [...PAGES.map((p) => p.id), "readers"];

/**
 * `#/sources` → the list; `#/sources/dry` → one source; `#/sources/dry/humidity`
 * → one channel; `#/actuators/pumps`, `#/loops/pumps`, `#/readers/sht4x`,
 * `#/sessions/4` → one of each.
 */
export interface Route {
  page: Page;
  /** The thing's name (a session's id as text), or `null` for the list page. */
  name: string | null;
  /** For a channel: the measurand. */
  measurand: string | null;
}

const fromHash = (): Route => {
  const [id, name, measurand] = window.location.hash
    .replace(/^#\/?/, "")
    .split("/")
    .map((s) => decodeURIComponent(s));
  const page = ALL_PAGES.includes(id as Page) ? (id as Page) : "overview";
  return { page, name: name || null, measurand: page === "sources" && measurand ? measurand : null };
};

export const hashFor = (page: Page, name: string | number | null = null, measurand: string | null = null) =>
  `#/${page}${name === null ? "" : `/${encodeURIComponent(String(name))}`}${measurand === null ? "" : `/${encodeURIComponent(measurand)}`}`;

/** The app's routes, for the library's `<Ref>` links and for the app's own cards. */
export const hrefFor: HrefFor = (ref) => {
  switch (ref.kind) {
    case "source":
      return hashFor("sources", ref.name);
    case "channel":
      return hashFor("sources", ref.name, ref.measurand ?? null);
    case "actuator":
      return hashFor("actuators", ref.name);
    case "reader":
      return hashFor("readers", ref.name);
    case "loop":
      return hashFor("loops", ref.name);
    case "session":
      return hashFor("sessions", ref.name);
    default:
      return undefined;
  }
};

/** Hash routing. No dependency, works when the daemon serves the bundle from any path. */
export function useRoute(): [Route, (page: Page, name?: string | number | null, measurand?: string | null) => void] {
  const [route, setRoute] = useState<Route>(fromHash);
  useEffect(() => {
    const onHash = () => setRoute(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return [route, (page, name = null, measurand = null) => (window.location.hash = hashFor(page, name, measurand))];
}
