import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { Alert, Snackbar } from "@mui/material";
import { RigError, type Condition } from "@flyball/client";
import { useHealth, useRig } from "@flyball/react";
import { Confirm } from "./Confirm.js";
import { confirmLevel } from "./confirmLevels.js";

interface Holds {
  /** Each controller's own conditions now, by name. */
  byController: Map<string, Condition[]>;
  refresh(): void;
}

const HoldsContext = createContext<Holds | null>(null);

/** One `/api/health` read for a page of faceplates: each controller's conditions (a latch, a permissive holding it). */
export function ControllerHoldsProvider({ children }: { children: ReactNode }) {
  const health = useHealth(5000);
  const value = useMemo<Holds>(() => {
    const byController = new Map<string, Condition[]>();
    for (const c of health.data?.conditions ?? []) if (c.scope === "controller") byController.set(c.subject, [...(byController.get(c.subject) ?? []), c]);
    return { byController, refresh: health.refresh };
  }, [health.data, health.refresh]);
  return <HoldsContext.Provider value={value}>{children}</HoldsContext.Provider>;
}

/**
 * What a faceplate needs to say why its controller cannot regulate, and to let its own fault latch
 * go: its conditions, `onReset` (behind a confirmation), and the dialog to render.
 */
export function useControllerHolds(name: string): { conditions: Condition[]; onReset(cause: string): void; dialog: ReactNode } {
  const holds = useContext(HoldsContext);
  const rig = useRig();
  const [cause, setCause] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reset = async (which: string) => {
    setBusy(true);
    try {
      await rig.resetRig(which);
      setCause(null);
      holds?.refresh();
    } catch (e) {
      setError(e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const dialog = (
    <>
      <Confirm
        open={cause !== null}
        title={`Reset ${name}'s fault latch?`}
        text="Lets the latch go and clears the controller's law state. It stays in manual until someone sets it regulating."
        action="Reset"
        danger={false}
        level={confirmLevel("rig.reset")}
        busy={busy}
        onClose={() => setCause(null)}
        onConfirm={() => cause && void reset(cause)}
      />
      <Snackbar open={error !== null} onClose={() => setError(null)}>
        {error ? (
          <Alert severity="error" onClose={() => setError(null)}>
            {error}
          </Alert>
        ) : undefined}
      </Snackbar>
    </>
  );
  return {
    conditions: holds?.byController.get(name) ?? [],
    onReset: (which) => (confirmLevel("rig.reset") === 0 ? void reset(which) : setCause(which)),
    dialog,
  };
}
