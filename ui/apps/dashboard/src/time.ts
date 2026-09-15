import { useEffect, useState } from "react";

export const duration = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
};

export const clock = (ns: number) => new Date(ns / 1e6).toLocaleTimeString();

export const when = (ns: number) => new Date(ns / 1e6).toLocaleString();

/** The current time in ms, re-rendering every `everyMs`; for a running duration. */
export function useNow(everyMs = 1000) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}
