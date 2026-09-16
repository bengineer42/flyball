import { useEffect, useRef, useState } from "react";
import type { HrefFor } from "@flyball/react";

export type Page = "overview" | "dashboards" | "sources" | "graph" | "actuators" | "loops" | "controllers" | "programs" | "events" | "sessions" | "simulation" | "readers";
/** The pages in the navigation; `readers` only exists as a detail page. Dashboards leads (spec §2: dashboard identity comes first), Overview second.
 * `actuators` and `loops` are no longer navigated to directly -- a loop is always bound to exactly one actuator, so the
 * two pages merged into one "Controllers" page (one card per actuator). Their ids stay in `Page`/`ALL_PAGES` only so an
 * old `#/loops`/`#/actuators` link still parses and `App.tsx` can redirect it to the equivalent `#/controllers` one. */
export const PAGES: Array<{ id: Exclude<Page, "readers" | "actuators" | "loops">; label: string }> = [
  { id: "dashboards", label: "Dashboards" },
  { id: "overview", label: "Overview" },
  { id: "sources", label: "Sources" },
  { id: "graph", label: "Graph" },
  { id: "controllers", label: "Controllers" },
  { id: "programs", label: "Programs" },
  { id: "events", label: "Events" },
  { id: "sessions", label: "Sessions" },
  { id: "simulation", label: "Simulation" },
];
const ALL_PAGES: Page[] = [...PAGES.map((p) => p.id), "readers", "actuators", "loops"];

/**
 * `#/sources` → the list; `#/sources/dry` → one source; `#/sources/dry/humidity`
 * → one channel; `#/controllers/pumps`, `#/readers/sht4x`, `#/programs/my-control`,
 * `#/sessions/4`, `#/dashboards/firing` → one of each. `#/actuators[/x]` and
 * `#/loops[/x]` still parse (as their own `page`) so `App.tsx` can redirect them.
 */
export interface Route {
  page: Page;
  /** The thing's name (a session's id as text), or `null` for the list page. */
  name: string | null;
  /** For a channel: the measurand. */
  measurand: string | null;
  /** `#/events?level=WARNING` → `{level: "WARNING"}`; empty when the hash has no query. */
  params: Record<string, string>;
}

const fromHash = (): Route => {
  const [path = "", search] = window.location.hash.replace(/^#\/?/, "").split("?");
  const [id, name, measurand] = path.split("/").map((s) => decodeURIComponent(s));
  const page = ALL_PAGES.includes(id as Page) ? (id as Page) : "overview";
  return { page, name: name || null, measurand: page === "sources" && measurand ? measurand : null, params: Object.fromEntries(new URLSearchParams(search ?? "")) };
};

/** The same page, name and measurand: a navigation that should scroll to the top, as opposed to a query change. */
export const samePlace = (a: Route, b: Route) => a.page === b.page && a.name === b.name && a.measurand === b.measurand;

export const hashFor = (page: Page, name: string | number | null = null, measurand: string | null = null, params: Record<string, string> = {}) => {
  const query = new URLSearchParams(params).toString();
  return `#/${page}${name === null ? "" : `/${encodeURIComponent(String(name))}`}${measurand === null ? "" : `/${encodeURIComponent(measurand)}`}${query ? `?${query}` : ""}`;
};

/** The app's routes, for the library's `<Ref>` links and for the app's own cards. */
export const hrefFor: HrefFor = (ref) => {
  switch (ref.kind) {
    case "source":
      return hashFor("sources", ref.name);
    case "channel":
      return hashFor("sources", ref.name, ref.measurand ?? null);
    case "actuator":
      return hashFor("controllers", ref.name);
    case "reader":
      return hashFor("readers", ref.name);
    case "loop":
      return hashFor("controllers", ref.name);
    case "session":
      return hashFor("sessions", ref.name);
    default:
      return undefined;
  }
};

/** `#/loops[/x]` and `#/actuators[/x]` → the equivalent `#/controllers[/x]` hash (a loop is named after the actuator it drives); `null` for any other page. */
export const legacyControllerRedirect = (route: Route): string | null =>
  route.page === "loops" || route.page === "actuators" ? hashFor("controllers", route.name, null, route.params) : null;

/**
 * Hash routing. No dependency, works when the daemon serves the bundle from
 * any path. Moving to another place (page, name or measurand) scrolls to the
 * top; a query change or a re-render of the same place leaves the scroll alone.
 */
export function useRoute(): [Route, (page: Page, name?: string | number | null, measurand?: string | null) => void] {
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
        window.location.hash = hashFor(prev.page, prev.name, prev.measurand, prev.params);
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
  return [route, (page, name = null, measurand = null) => (window.location.hash = hashFor(page, name, measurand))];
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
