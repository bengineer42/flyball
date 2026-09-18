import { useCallback, useMemo, useState } from "react";
import { useRig } from "../provider.js";
import { useQuery } from "./useQuery.js";
import { useNowS } from "../store/hooks.js";

/** How far one rewind/fast-forward step moves, in rig seconds. */
export const PLAYBACK_STEP_S = 30;

export interface PlaybackHook {
  /** True while showing history rather than the live stream. */
  paused: boolean;
  /** The open (scratch) session this scrubs through, or undefined before it loads. */
  sessionId: number | undefined;
  /** The session's start, in rig seconds since the epoch. */
  startS: number | undefined;
  /** The rig's current time, in rig seconds since the epoch (`useNowS`). */
  nowS: number;
  /**
   * Where playback is reading from, in rig seconds. `nowS` while live; the
   * paused position otherwise. Always within `[startS, nowS]`.
   */
  atS: number;
  /** Jump back `PLAYBACK_STEP_S` (or `byS`) and pause. */
  rewind(byS?: number): void;
  /** Jump forward `PLAYBACK_STEP_S` (or `byS`); resumes live on reaching `nowS`. */
  fastForward(byS?: number): void;
  /** Pause exactly where playback is now. */
  pause(): void;
  /** Go straight to live and stop reading history. */
  resume(): void;
  /** Scrub to an absolute rig-seconds position; clamps into range, pauses unless it lands on `nowS`. */
  seek(atS: number): void;
  loading: boolean;
  error: Error | undefined;
}

/**
 * A video-style transport over the rig's own recorded history: play (live),
 * pause, rewind, fast-forward and scrub. Bounds (`sessionId`/`startS`) come
 * from `/api/recording`; a consumer reads the samples themselves from
 * `/api/history` on `atS` -- no new recorder or storage work, that already
 * keeps a rolling record of everything, this only reads it back. Read-only
 * by construction: nothing here writes a demand or a setpoint, so scrubbing
 * back never risks touching the running rig.
 *
 * `atS`/`paused` are the extent of what this hook decides; which widgets
 * honour them (fetch `rig.series(sessionId, address, {start_ns, end_ns})`
 * around `atS` instead of reading the live store) is the next piece, not
 * built here yet -- see brain/tasks/sim-scrubber.md.
 */
export function usePlayback(): PlaybackHook {
  const rig = useRig();
  const nowS = useNowS();
  const recording = useQuery(() => rig.recording(), [rig], { refreshMs: 5000 });
  const sessionId = recording.data?.id;
  const startS = recording.data ? recording.data.start_ns / 1e9 : undefined;

  const [atS, setAtS] = useState<number | null>(null); // null: live

  const clamp = useCallback(
    (t: number) => Math.min(nowS, Math.max(startS ?? t, t)),
    [nowS, startS],
  );

  const seek = useCallback(
    (t: number) => {
      const clamped = clamp(t);
      setAtS(clamped >= nowS ? null : clamped);
    },
    [clamp, nowS],
  );

  const pause = useCallback(() => setAtS((prev) => clamp(prev ?? nowS)), [clamp, nowS]);
  const resume = useCallback(() => setAtS(null), []);
  const rewind = useCallback((byS: number = PLAYBACK_STEP_S) => seek((atS ?? nowS) - byS), [atS, nowS, seek]);
  const fastForward = useCallback((byS: number = PLAYBACK_STEP_S) => seek((atS ?? nowS) + byS), [atS, nowS, seek]);

  return useMemo(
    () => ({
      paused: atS !== null,
      sessionId,
      startS,
      nowS,
      atS: atS ?? nowS,
      rewind,
      fastForward,
      pause,
      resume,
      seek,
      loading: recording.loading,
      error: recording.error,
    }),
    [atS, sessionId, startS, nowS, rewind, fastForward, pause, resume, seek, recording.loading, recording.error],
  );
}
