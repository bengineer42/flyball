import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Alert, Snackbar } from "@mui/material";
import { RigClient, RigError, type EditOptions, type RigEditOut, type Transport } from "@flyball/client";
import { Confirm } from "./Confirm.js";

/**
 * A rig edit (adding or removing a link or device, restoring a version) saves a new version and
 * restarts the runner on it (D-051). What a person is told before applying one.
 */
export const RESTART_TEXT =
  "Applying restarts the rig: outputs go to their stop, controllers come back in manual, a running program is cancelled, and a recording goes on in a new session.";

/** How long a restart may take before the page says the rig has not come back. */
const BACK_WITHIN_MS = 120_000;
const POLL_MS = 1_000;

type Watch =
  | { kind: "restarting"; edit: RigEditOut; since: number }
  | { kind: "back"; edit: RigEditOut }
  | { kind: "failed"; edit: RigEditOut; message: string };

const TrackContext = createContext<((edit: RigEditOut) => void) | null>(null);

/**
 * Watches a rig edit's restart for the whole app: a banner while the runner is away, then, once a
 * runner started after the edit answers, `onBack` (the app rebuilds its connection and every page
 * reads the rig afresh) and a line saying which version it is on -- or why it is not. Lives above
 * `RigProvider`, so it outlasts the rebuild it asks for.
 */
export function RigEditProvider({ transport, onBack, children }: { transport: Transport; onBack(): void; children: ReactNode }) {
  const [watch, setWatch] = useState<Watch | null>(null);
  const client = useRef(new RigClient(transport));
  client.current = new RigClient(transport);
  const back = useRef(onBack);
  back.current = onBack;

  const track = useCallback((edit: RigEditOut) => setWatch({ kind: "restarting", edit, since: Date.now() }), []);

  useEffect(() => {
    if (watch?.kind !== "restarting") return;
    let stopped = false;
    const { edit, since } = watch;
    // The runner is back once it has been away (the old process gone), or once the rig's clock
    // started again (a restart too quick to catch it away). The rig's uptime will not do: it runs
    // on the rig's clock, sixty times fast on a ×60 simulation.
    let sawAway = false;
    let startedNs: number | null = null;
    void client.current.clock().then(
      (c) => (startedNs = c.start_time_ns),
      () => (sawAway = true),
    );
    const poll = async () => {
      if (stopped) return;
      const waited = Date.now() - since;
      try {
        const [health, clock] = await Promise.all([client.current.health(), client.current.clock()]);
        if (sawAway || (startedNs !== null && clock.start_time_ns !== startedNs)) {
          const failed = health.conditions.find((c) => c.code === "edit_not_built");
          stopped = true;
          back.current();
          setWatch(
            failed
              ? { kind: "failed", edit, message: `Version ${edit.version} could not be built, so the rig went back to ${edit.previous === null ? "the one before" : `version ${edit.previous}`}. ${failed.message}` }
              : { kind: "back", edit },
          );
          return;
        }
      } catch {
        // Away while it restarts: connection refused, 502 or 503 from a front. Keep asking.
        sawAway = true;
      }
      if (waited > BACK_WITHIN_MS) {
        stopped = true;
        setWatch({ kind: "failed", edit, message: `The rig has not come back ${Math.round(BACK_WITHIN_MS / 60_000)} min after the edit. Its log says why; reload this page once it is running.` });
        return;
      }
      setTimeout(() => void poll(), POLL_MS);
    };
    const first = setTimeout(() => void poll(), POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(first);
    };
  }, [watch]);

  return (
    <TrackContext.Provider value={track}>
      {children}
      <Snackbar open={watch !== null} anchorOrigin={{ vertical: "top", horizontal: "center" }} autoHideDuration={watch?.kind === "back" ? 6000 : null} onClose={(_, why) => (why === "clickaway" ? undefined : setWatch(null))}>
        {watch ? (
          <Alert
            severity={watch.kind === "restarting" ? "info" : watch.kind === "back" ? "success" : "error"}
            onClose={watch.kind === "restarting" ? undefined : () => setWatch(null)}
            data-testid="rig-edit-status"
            sx={{ maxWidth: 640 }}
          >
            {watch.kind === "restarting" && `Restarting the rig on version ${watch.edit.version} (${watch.edit.reason})…`}
            {watch.kind === "back" && `The rig is back on version ${watch.edit.version}: controllers are in manual.`}
            {watch.kind === "failed" && watch.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </TrackContext.Provider>
  );
}

const PROGRAM_RUNNING = /program is running/i;

/**
 * Applying a rig edit from a page: `apply(run)` sends it and hands a 202 to the app-wide watch.
 * A 409 because a program is running opens `dialog`, which offers to cancel the program and
 * apply anyway; any other refusal is thrown for the page to show where the edit was made.
 * Resolves to the edit, or null when the person declined to force it.
 */
export function useRigEdit(): { apply(run: (options: EditOptions) => Promise<RigEditOut>): Promise<RigEditOut | null>; dialog: ReactNode } {
  const track = useContext(TrackContext);
  const [pending, setPending] = useState<{ detail: string; run: (options: EditOptions) => Promise<RigEditOut>; resolve(edit: RigEditOut | null): void; reject(e: unknown): void } | null>(null);
  const [busy, setBusy] = useState(false);

  const sent = useCallback(
    (edit: RigEditOut) => {
      track?.(edit);
      return edit;
    },
    [track],
  );

  const apply = useCallback(
    async (run: (options: EditOptions) => Promise<RigEditOut>) => {
      try {
        return sent(await run({}));
      } catch (e) {
        if (e instanceof RigError && e.status === 409 && PROGRAM_RUNNING.test(e.detail)) {
          return new Promise<RigEditOut | null>((resolve, reject) => setPending({ detail: e.detail, run, resolve, reject }));
        }
        throw e;
      }
    },
    [sent],
  );

  const dialog = (
    <Confirm
      open={pending !== null}
      title="A program is running"
      text="A rig edit stops the rig, which cancels the running program. Cancel it and apply the edit?"
      action="Cancel the program and apply"
      busy={busy}
      onClose={() => {
        pending?.resolve(null);
        setPending(null);
      }}
      onConfirm={() => {
        if (!pending) return;
        setBusy(true);
        pending
          .run({ force: true })
          .then((edit) => pending.resolve(sent(edit)), pending.reject)
          .finally(() => {
            setBusy(false);
            setPending(null);
          });
      }}
    />
  );

  return { apply, dialog };
}
