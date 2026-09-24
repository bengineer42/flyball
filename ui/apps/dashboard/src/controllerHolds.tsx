import { useState, type ReactNode } from "react";
import { Alert, Snackbar } from "@mui/material";
import { RigError } from "@flyball/client";
import { useRig } from "@flyball/react";
import { Confirm } from "./Confirm.js";
import { confirmLevel } from "./confirmLevels.js";

/**
 * A faceplate's Reset for its own fault latch: `onReset` (behind a confirmation) and the dialog to
 * render. The latch itself the faceplate reads from the store's conditions.
 */
export function useControllerHolds(name: string): { onReset(cause: string): void; dialog: ReactNode } {
  const rig = useRig();
  const [cause, setCause] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reset = async (which: string) => {
    setBusy(true);
    try {
      await rig.resetRig(which);
      setCause(null);
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
    onReset: (which) => (confirmLevel("rig.reset") === 0 ? void reset(which) : setCause(which)),
    dialog,
  };
}
