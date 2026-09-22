import { useEffect, useState, type RefObject } from "react";

/**
 * Whether an element is worth drawing into: on screen (IntersectionObserver)
 * and in a tab the viewer is looking at (`document.visibilityState`). A chart
 * that is scrolled off or in a background tab can hold its data back until
 * it is seen again; what it missed is in the arrays it will be given then.
 * `true` where the observer is unavailable, so nothing is ever left blank.
 *
 * `initial` is the value assumed before the observer has reported: `true`
 * (the default) so a chart already fed data (the live dashboards) paints on
 * first render rather than waiting a frame. A consumer that gates a *fetch*
 * on this (a session's charts, lazily loading their series) wants `false`
 * instead -- an optimistic `true` there would fire the request for every
 * tile the moment it mounts, defeating the laziness.
 */
export function useVisible(ref: RefObject<Element | null>, rootMargin = "200px", initial = true): boolean {
  const [onScreen, setOnScreen] = useState(initial);
  const [tabShown, setTabShown] = useState(() => typeof document === "undefined" || document.visibilityState !== "hidden");

  useEffect(() => {
    const el = ref.current;
    // No element yet, or no observer in this environment (a test, SSR): assume visible regardless
    // of `initial`, so a caller gating a fetch on this hook is never left waiting forever.
    if (!el || typeof IntersectionObserver === "undefined") {
      setOnScreen(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => setOnScreen(entry?.isIntersecting ?? true), { rootMargin });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, rootMargin]);

  useEffect(() => {
    const onChange = () => setTabShown(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  return onScreen && tabShown;
}
