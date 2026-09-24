import { useEffect, useRef, useState } from "react";
import type { HrefFor } from "@flyball/react";

export type Page = "dashboards" | "readings" | "graph" | "controllers" | "devices" | "programs" | "events" | "sessions" | "simulation" | "options";
/**
 * Every route and the title its app bar shows. There is no sidebar (D-053): dashboards are tabs in
 * the app bar, the status chips lead to Events, Sessions, Programs and Simulation, and the gear to
 * Options, whose Pages tab links the rest. `devices` is a device's own page (`#/devices/<name>`);
 * the list of every device and signal is Readings. Old addresses still work: see `LEGACY`.
 */
export const PAGES: Array<{ id: Page; label: string }> = [
  { id: "dashboards", label: "Dashboards" },
  { id: "readings", label: "Readings" },
  { id: "devices", label: "Devices" },
  { id: "graph", label: "Graph" },
  { id: "controllers", label: "Controllers" },
  { id: "programs", label: "Programs" },
  { id: "events", label: "Events" },
  { id: "sessions", label: "Sessions" },
  { id: "simulation", label: "Simulation" },
  { id: "options", label: "Options" },
];
const ALL_PAGES: Page[] = PAGES.map((p) => p.id);

/**
 * Addresses from before D-053, and where each now lives: a bookmark, a dashboard `link` widget or
 * a note keeps working. The Overview page became the generated dashboard; Inputs became Readings,
 * which also took the Devices list; Config became the Options page's Rig file tab.
 */
type Moved = { page: Page; name: string | null; params?: Record<string, string> };
const LEGACY: Record<string, (name: string | undefined) => Moved> = {
  overview: () => ({ page: "dashboards", name: null, params: { generated: "" } }),
  inputs: (name) => ({ page: "readings", name: name || null }),
  rig: () => ({ page: "options", name: "rig" }),
};

/**
 * `#/inputs` → every publishing signal; `#/inputs/furnace.zone1` → one signal
 * by address; `#/devices/furnace` → one device; `#/controllers/heaters.heater1`
 * → one controller (named by the address of the signal it drives);
 * `#/programs/my-control`, `#/sessions/4`, `#/dashboards/firing` → one of each.
 * An address has no slashes, so it is one path segment.
 */
export interface Route {
  page: Page;
  /** The thing's name (a session's id as text, a signal's or controller's address), or `null` for the list page. */
  name: string | null;
  /** `#/events?level=WARNING` → `{level: "WARNING"}`; empty when the hash has no query. */
  params: Record<string, string>;
}

const fromHash = (): Route => {
  const [path = "", search] = window.location.hash.replace(/^#\/?/, "").split("?");
  const [id = "", name] = path.split("/").map((s) => decodeURIComponent(s));
  const params = Object.fromEntries(new URLSearchParams(search ?? ""));
  const legacy = LEGACY[id] ?? (id === "devices" && !name ? (): Moved => ({ page: "readings", name: null }) : undefined);
  if (legacy) {
    const to = legacy(name);
    const route = { page: to.page, name: to.name, params: { ...params, ...(to.params ?? {}) } };
    window.location.replace(hashFor(route.page, route.name, route.params));
    return route;
  }
  // Anything else unknown, and the bare `#/`, open the dashboards.
  const page = ALL_PAGES.includes(id as Page) ? (id as Page) : "dashboards";
  return { page, name: name || null, params };
};

/** The same page and name: a navigation that should scroll to the top, as opposed to a query change. */
export const samePlace = (a: Route, b: Route) => a.page === b.page && a.name === b.name;

export const hashFor = (page: Page, name: string | number | null = null, params: Record<string, string> = {}) => {
  const query = new URLSearchParams(params).toString();
  return `#/${page}${name === null ? "" : `/${encodeURIComponent(String(name))}`}${query ? `?${query}` : ""}`;
};

/** The app's routes, for the library's `<Ref>` links and for the app's own cards. */
export const hrefFor: HrefFor = (ref) => {
  switch (ref.kind) {
    case "device":
      return hashFor("devices", ref.name);
    case "signal":
      return hashFor("readings", ref.name);
    case "controller":
      return hashFor("controllers", ref.name);
    case "session":
      return hashFor("sessions", ref.name);
    case "event":
      return hashFor("events");
    default:
      return undefined;
  }
};

/**
 * Hash routing. No dependency, works when the runner serves the bundle from
 * any path. Moving to another place (page or name) scrolls to the top; a
 * query change or a re-render of the same place leaves the scroll alone.
 */
export function useRoute(): [Route, (page: Page, name?: string | number | null) => void] {
  const [route, setRoute] = useState<Route>(fromHash);
  const current = useRef(route);
  useEffect(() => {
    let restoring = false;
    const onHash = () => {
      if (restoring) {
        restoring = false;
        return;
      }
      const next = fromHash();
      const prev = current.current;
      // A page with unsaved work may refuse to be left: put the hash back and stay.
      const free = unguarded;
      unguarded = false;
      if (!free && !samePlace(prev, next) && [...guards].some((blocks) => blocks())) {
        restoring = true;
        window.location.hash = hashFor(prev.page, prev.name, prev.params);
        return;
      }
      current.current = next;
      setRoute(() => {
        if (!samePlace(prev, next)) window.scrollTo(0, 0);
        return next;
      });
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return [route, (page, name = null) => (window.location.hash = hashFor(page, name))];
}

/** Each returns true to keep the viewer where they are. */
const guards = new Set<() => boolean>();
let unguarded = false;

/** The next hash change goes through without asking: for a page's own navigation after it saved or discarded its work. */
export const leaveFreely = () => {
  unguarded = true;
};

/**
 * While `when`, leaving the current place (another page, another name) asks
 * `message` first, and closing or reloading the tab gets the browser's own
 * prompt. For a page with unsaved edits.
 */
export function useLeaveGuard(when: boolean, message: string) {
  useEffect(() => {
    if (!when) return;
    const guard = () => !window.confirm(message);
    guards.add(guard);
    const unload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", unload);
    return () => {
      guards.delete(guard);
      window.removeEventListener("beforeunload", unload);
    };
  }, [when, message]);
}

const SCROLL_KEY = "flyball.scroll";

/**
 * Keep the scroll position across a full page load (F5, the dev server's
 * reloads): the app renders "loading…" first, so the browser's own
 * restoration lands on an empty page. Call with `ready` once the page has
 * its content; the position is put back only when the hash is the one it
 * was saved for.
 */
export function useScrollMemory(ready: boolean) {
  useEffect(() => {
    try {
      window.history.scrollRestoration = "manual";
    } catch {
      /* not supported */
    }
    let pending: number | null = null;
    const save = () => {
      if (pending !== null) return;
      pending = window.setTimeout(() => {
        pending = null;
        try {
          window.sessionStorage.setItem(SCROLL_KEY, JSON.stringify({ hash: window.location.hash, y: window.scrollY }));
        } catch {
          /* not persisted */
        }
      }, 150);
    };
    window.addEventListener("scroll", save, { passive: true });
    return () => {
      window.removeEventListener("scroll", save);
      if (pending !== null) window.clearTimeout(pending);
    };
  }, []);

  useEffect(() => {
    if (!ready) return;
    let saved: { hash: string; y: number } | null = null;
    try {
      saved = JSON.parse(window.sessionStorage.getItem(SCROLL_KEY) ?? "null") as { hash: string; y: number } | null;
    } catch {
      saved = null;
    }
    if (!saved || saved.hash !== window.location.hash || !(saved.y > 0)) return;
    // The page grows for a moment as panels and charts mount; keep trying until it is tall enough or a second has passed.
    const target = saved.y;
    const started = Date.now();
    let id = 0;
    const attempt = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      if (max >= target || Date.now() - started > 1000) {
        window.scrollTo(0, Math.min(target, Math.max(0, max)));
        return;
      }
      id = window.requestAnimationFrame(attempt);
    };
    id = window.requestAnimationFrame(attempt);
    return () => window.cancelAnimationFrame(id);
    // Runs once, the first time the content is there.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);
}
