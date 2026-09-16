import { useEffect, useState, type RefObject } from "react";

/**
 * Whether an element is worth drawing into: on screen (IntersectionObserver)
 * and in a tab the viewer is looking at (`document.visibilityState`). A chart
 * that is scrolled off or in a background tab can hold its data back until
 * it is seen again; what it missed is in the arrays it will be given then.
 * `true` where the observer is unavailable, so nothing is ever left blank.
 */
export function useVisible(ref: RefObject<Element | null>, rootMargin = "200px"): boolean {
  const [onScreen, setOnScreen] = useState(true);
  const [tabShown, setTabShown] = useState(() => typeof document === "undefined" || document.visibilityState !== "hidden");

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
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
