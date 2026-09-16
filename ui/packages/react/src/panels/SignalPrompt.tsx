import { useEffect, useState } from "react";
import type { SignalState } from "@flyball/client";

export interface SignalPromptProps {
  /** Prompts still waiting for a person; nothing renders when empty. Pass `useSignals().pending`, not every signal. */
  signals: SignalState[];
  /** The rig's now, in seconds, for the "waiting for" clock; wall time when omitted. */
  nowS?: number;
  onFire: (name: string) => void | Promise<void>;
  /** Give up on the wait: the program treats it as interrupted. Omit to hide the button. */
  onInterrupt?: (name: string) => void | Promise<void>;
}

const clock = (s: number) => {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : m > 0 ? `${m} min ${r} s` : `${r} s`;
};

/**
 * What a program is waiting on a person for: the message, how long it has
 * been waiting, how long before it gives up, and the button that answers it.
 * Pure: the app decides where it goes (a banner on every page, so nobody
 * misses it) and what firing does.
 */
export function SignalPrompt({ signals, nowS, onFire, onInterrupt }: SignalPromptProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [wall, setWall] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (nowS !== undefined || !signals.length) return;
    const id = window.setInterval(() => setWall(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, [nowS, signals.length]);
  if (!signals.length) return null;
  const now = nowS ?? wall;
  const answer = async (fn: (name: string) => void | Promise<void>, name: string) => {
    setBusy(name);
    try {
      await fn(name);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="fb-signals" role="alert">
      {signals.map((s) => {
        const waitedS = Math.max(0, now - s.since_ns / 1e9);
        const leftS = s.timeout_s === null ? null : s.timeout_s - waitedS;
        return (
          <div key={s.name} className="fb-signal">
            <span className="fb-signal-icon" aria-hidden="true">⏸</span>
            <div className="fb-signal-text">
              <strong>{s.message || `waiting for ${s.name}`}</strong>
              <span className="fb-muted">
                {" "}
                · waiting {clock(waitedS)}
                {leftS !== null && (leftS > 0 ? ` · gives up in ${clock(leftS)}` : " · timed out")}
              </span>
            </div>
            <button type="button" className="fb-signal-go" disabled={busy === s.name} onClick={() => answer(onFire, s.name)}>
              Go
            </button>
            {onInterrupt && (
              <button type="button" className="fb-signal-skip" disabled={busy === s.name} onClick={() => answer(onInterrupt, s.name)} title="Stop waiting; the program sees this step as interrupted">
                Abandon
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
