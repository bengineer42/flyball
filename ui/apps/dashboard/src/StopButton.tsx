import { useEffect, useState } from "react";
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, Snackbar, Alert, Table, TableBody, TableCell, TableHead, TableRow, Tooltip, Typography } from "@mui/material";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import { RigError, type OutputStopOut, type StopPlan, type StopReport } from "@flyball/client";
import { useRig } from "@flyball/react";
import { Confirm } from "./Confirm.js";
import { confirmLevel } from "./confirmLevels.js";
import { useAuth } from "./auth.js";

/** What the stop did, in a sentence: how many devices stopped, kept or failed, and whether the rig is latched. */
export function stopSummary(report: StopReport): { severity: "success" | "warning"; message: string } {
  const devices = Object.entries(report.devices ?? {});
  if (report.interim) return { severity: "warning", message: "Software stop: controllers to manual; nothing written (this runner's stop is the interim one)" };
  const count = (state: string) => devices.filter(([, d]) => d.state === state).length;
  const failed = devices.filter(([, d]) => d.state === "failed");
  const parts = [`${count("stopped")} stopped`, `${count("unchanged")} unchanged`];
  if (failed.length) parts.push(`${failed.length} failed (${failed.map(([name, d]) => (d.message ? `${name}: ${d.message}` : name)).join("; ")})`);
  const latch = report.latched ? " The rig is latched until a person resets it." : "";
  return { severity: failed.length ? "warning" : "success", message: `Software stop: ${parts.join(", ")}.${latch}` };
}

/** What a stop does to one output, as a person reads it: `off (0)`, `20 (you said)`, `its stop command`, `kept`. */
export function describeOutputStop(output: OutputStopOut): string {
  if (output.origin === "command") return `runs ${output.command ?? "its stop command"}`;
  if (output.stop === "keep") return output.origin === "nobody_said" ? "kept (nobody said)" : "kept (you said)";
  if (output.stop === null) return "its stop command";
  return output.origin === "off" ? `off (${output.stop})` : `${output.stop} (you said)`;
}

/** The stop a Software stop would apply, output by output, for the confirmation: fetched as it opens. */
function PlanList({ plan }: { plan: StopPlan | null | "error" }) {
  if (plan === null) return <Typography variant="body2" color="text.secondary">Reading what a stop writes…</Typography>;
  if (plan === "error") return <Typography variant="body2" color="text.secondary">What a stop writes could not be read; the stop itself still works.</Typography>;
  if (plan.outputs.length === 0) return <Typography variant="body2" color="text.secondary">No writable output: the stop writes nothing.</Typography>;
  return (
    <Table size="small" data-testid="stop-plan" sx={{ mt: 1 }}>
      <TableBody>
        {plan.outputs.map((o) => (
          <TableRow key={o.address}>
            <TableCell sx={{ pl: 0 }}>{o.address}</TableCell>
            <TableCell>{describeOutputStop(o)}</TableCell>
            <TableCell sx={{ color: "warning.main" }}>{o.warnings.join("; ")}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

const shown = (values: Record<string, number | null> | undefined) =>
  values ? Object.entries(values).map(([address, v]) => `${address} = ${v === null ? "?" : v}`).join(", ") : "";

/** The stop's report device by device: state, what it wrote, what it kept, and why. */
function ReportDialog({ report, onClose }: { report: StopReport | null; onClose(): void }) {
  return (
    <Dialog open={report !== null} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>What the software stop did</DialogTitle>
      <DialogContent>
        {report && (
          <Table size="small" data-testid="stop-report">
            <TableHead>
              <TableRow>
                <TableCell>device</TableCell>
                <TableCell>state</TableCell>
                <TableCell>wrote</TableCell>
                <TableCell>kept</TableCell>
                <TableCell>message</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(report.devices ?? {}).map(([name, d]) => (
                <TableRow key={name}>
                  <TableCell>{name}</TableCell>
                  <TableCell sx={d.state === "failed" ? { color: "error.main", fontWeight: 600 } : undefined}>{d.state}</TableCell>
                  <TableCell>{shown(d.written)}</TableCell>
                  <TableCell>{shown(d.kept)}</TableCell>
                  <TableCell>{d.message}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {report && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
            {report.program_interrupted ? "The running program was interrupted. " : ""}
            {report.controllers_manual.length ? `In manual: ${report.controllers_manual.join(", ")}. ` : ""}
            {report.latched ? "The rig is latched until a person resets it." : ""}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
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
  const [report, setReport] = useState<StopReport | null>(null);
  const [details, setDetails] = useState(false);
  const [plan, setPlan] = useState<StopPlan | null | "error">(null);
  useEffect(() => {
    if (!confirming) return;
    let current = true;
    setPlan(null);
    rig.stopPlan().then(
      (p) => current && setPlan(p),
      () => current && setPlan("error"),
    );
    return () => {
      current = false;
    };
  }, [confirming, rig]);

  if (!canOperate) return null;

  const stop = async () => {
    setBusy(true);
    try {
      const answer = await rig.stopRig();
      setReport(answer);
      setResult(stopSummary(answer));
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
        level={confirmLevel("rig.stop")}
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={() => void stop()}
      >
        <PlanList plan={plan} />
      </Confirm>
      <ReportDialog report={details ? report : null} onClose={() => setDetails(false)} />
      <Snackbar open={result !== null} autoHideDuration={result?.severity === "success" ? 6000 : null} onClose={() => setResult(null)}>
        {result ? (
          <Alert
            severity={result.severity}
            onClose={() => setResult(null)}
            data-testid="stop-result"
            action={
              report && result.severity !== "error" ? (
                <>
                  <Button color="inherit" size="small" onClick={() => setDetails(true)} data-testid="stop-details">
                    Details
                  </Button>
                  <Button color="inherit" size="small" onClick={() => setResult(null)}>
                    Close
                  </Button>
                </>
              ) : undefined
            }
          >
            {result.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </>
  );
}
