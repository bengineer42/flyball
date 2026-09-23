import { useState } from "react";
import { Button, Snackbar, Alert, Tooltip } from "@mui/material";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import { RigError } from "@flyball/client";
import { useRig } from "@flyball/react";
import { Confirm } from "./Confirm.js";
import { useAuth } from "./auth.js";

/**
 * Stops the rig for everyone: interrupts any running program and puts every controller in manual
 * (`POST <root>/api/rig/stop`, answering a `StopReport`). Nothing is written to a device: outputs are
 * left as they were, and the report's `interim` says so. Stop needs `OPERATE`: a caller without it
 * never sees this button at all, so nothing here has to gate rendering a second time the way a
 * disabled control would. A refusal or failure is shown as one, in the server's own words, never as
 * a stop that happened.
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
      setResult({ ok: false, message: e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e) });
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
