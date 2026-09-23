import { useState } from "react";
import { Button, Snackbar, Alert, Tooltip } from "@mui/material";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import { RigError } from "@flyball/client";
import { useRig } from "@flyball/react";
import { Confirm } from "./Confirm.js";
import { useAuth } from "./auth.js";

/**
 * Stops the rig for everyone: interrupts any running program, puts every controller in manual and
 * holds every writable device (`POST <root>/api/rig/stop`, WP0-9's `StopReport`). Stop needs
 * `OPERATE` (decided): a caller without it never sees this button at all, so nothing here has to
 * gate rendering a second time the way a disabled control would.
 *
 * `POST /api/rig/stop` answers 501 `{"detail": ...}` until package A8 wires the real stopper --
 * this shows that honestly, as a failure, rather than reporting a stop that did not happen.
 */
export function StopButton() {
  const { canOperate } = useAuth();
  const rig = useRig();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  if (!canOperate) return null;

  const stop = async () => {
    setBusy(true);
    try {
      const report = await rig.stopRig();
      setResult({ ok: true, message: report.interim ? "Software stop: controllers to manual; nothing written — outputs left as they were" : "Software stop done" });
    } catch (e) {
      if (e instanceof RigError && e.status === 501) setResult({ ok: false, message: "Stop is not wired up yet on this runner" });
      else setResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <>
      <Tooltip title="Software stop: interrupt the program and put every controller in manual. Nothing is written -- outputs are left as they were.">
        <span>
          <Button
            variant="outlined"
            color="error"
            size="small"
            startIcon={<StopCircleOutlinedIcon fontSize="small" />}
            onClick={() => setConfirming(true)}
            disabled={busy}
            data-testid="stop-button"
          >
            Software stop
          </Button>
        </span>
      </Tooltip>
      <Confirm
        open={confirming}
        title="Software stop?"
        text="This interrupts any running program and puts every controller in manual. It writes nothing: outputs are left as they were."
        action="Software stop"
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={() => void stop()}
      />
      <Snackbar open={result !== null} autoHideDuration={6000} onClose={() => setResult(null)}>
        {result ? (
          <Alert severity={result.ok ? "success" : "error"} onClose={() => setResult(null)} data-testid="stop-result">
            {result.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </>
  );
}
