import { useState } from "react";
import { Button, Snackbar, Alert, Tooltip } from "@mui/material";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import { RigError, type StopReport } from "@flyball/client";
import { useRig } from "@flyball/react";
import { Confirm } from "./Confirm.js";
import { useAuth } from "./auth.js";

/** What the stop did, in a sentence: how many devices stopped, kept or failed, and whether the rig is latched. */
export function stopSummary(report: StopReport): { severity: "success" | "warning"; message: string } {
  const devices = Object.entries(report.devices ?? {});
  if (report.interim) return { severity: "warning", message: "Software stop: controllers to manual; nothing written (this runner's stop is the interim one)" };
  const count = (state: string) => devices.filter(([, d]) => d.state === state).length;
  const failed = devices.filter(([, d]) => d.state === "failed");
  const parts = [`${count("stopped")} stopped`, `${count("unchanged")} unchanged`];
  if (failed.length) parts.push(`${failed.length} failed (${failed.map(([name, d]) => (d.detail ? `${name}: ${d.detail}` : name)).join("; ")})`);
  const latch = report.latched ? " The rig is latched until a person resets it." : "";
  return { severity: failed.length ? "warning" : "success", message: `Software stop: ${parts.join(", ")}.${latch}` };
}

/**
 * Stops the rig for everyone: latches it, interrupts any running program, puts every controller in
 * manual and writes each device's stop (`POST <root>/api/rig/stop`, answering a `StopReport` whose
 * devices say stopped, unchanged or failed). Stop needs `OPERATE`: a caller without it
 * never sees this button at all, so nothing here has to gate rendering a second time the way a
 * disabled control would. A refusal or failure is shown as one, in the server's own words, never as
 * a stop that happened.
 */
export function StopButton() {
  const { canOperate } = useAuth();
  const rig = useRig();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ severity: "success" | "warning" | "error"; message: string } | null>(null);

  if (!canOperate) return null;

  const stop = async () => {
    setBusy(true);
    try {
      setResult(stopSummary(await rig.stopRig()));
    } catch (e) {
      setResult({ severity: "error", message: e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <>
      <Tooltip title="Software stop: interrupt the program, put every controller in manual and write each device's stop. The rig stays latched until a person resets it.">
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
        text="This interrupts any running program, puts every controller in manual and writes each device's stop (its stop command, else its stop values, else off; a device set to keep is left as it is). The rig stays latched until a person resets it."
        action="Software stop"
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={() => void stop()}
      />
      <Snackbar open={result !== null} autoHideDuration={result?.severity === "success" ? 6000 : null} onClose={() => setResult(null)}>
        {result ? (
          <Alert severity={result.severity} onClose={() => setResult(null)} data-testid="stop-result">
            {result.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </>
  );
}
