/**
 * Run a step now, without making a program of it: the program editor's own palette and step
 * forms in a dialog, and a Run button. What it runs is a program document that is never stored
 * (`POST /api/programs/run`), so it shows on the program chip and in Events like any program,
 * named `ONE_OFF`. The rig checks it as it is filled in, exactly as it checks a program.
 */
import { useEffect, useState } from "react";
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Typography } from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { useRig } from "@flyball/react";
import { RigError } from "@flyball/client";
import { useAuth } from "../auth.js";
import { briefError, stepOfError, type ProgramTree } from "../programDoc.js";
import { ProgramBuilder } from "./ProgramBuilder.js";
import { useProgramEditorInputs } from "./Programs.js";

/** The name the one-off run goes by, on the program chip and in Events. */
export const ONE_OFF = "one-off step";
const CHECK_DEBOUNCE_MS = 300;

interface Check {
  ok: boolean;
  stepErrors: Record<number, string>;
  stepWarnings: Record<number, string>;
  error: string | null;
}

export interface RunStepDialogProps {
  open: boolean;
  /** What the programmer is running now, or null: running a step then cancels it, and the dialog says so first. */
  busyProgram: string | null;
  onClose(): void;
  /** After a run started. */
  onRan?(): void;
}

export function RunStepDialog({ open, busyProgram, onClose, onRan }: RunStepDialogProps) {
  const rig = useRig();
  const { canOperate } = useAuth();
  const { programSchema, controllers, devices } = useProgramEditorInputs();
  const [tree, setTree] = useState<ProgramTree>({ name: ONE_OFF, steps: [] });
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The rig checks what is there as it is filled in, as the program editor does.
  useEffect(() => {
    if (tree.steps.length === 0) {
      setCheck(null);
      return;
    }
    let live = true;
    const handle = window.setTimeout(async () => {
      let result: Check;
      try {
        const { warnings } = await rig.checkProgram(tree);
        result = { ok: true, stepErrors: {}, stepWarnings: Object.fromEntries(Object.entries(warnings ?? {}).map(([i, m]) => [Number(i), m])), error: null };
      } catch (e) {
        const { index, message } = stepOfError(e);
        result = index !== null ? { ok: false, stepErrors: { [index]: briefError(message) }, stepWarnings: {}, error: null } : { ok: false, stepErrors: {}, stepWarnings: {}, error: briefError(message) };
      }
      if (live) setCheck(result);
    }, CHECK_DEBOUNCE_MS);
    return () => {
      live = false;
      window.clearTimeout(handle);
    };
  }, [rig, tree]);

  const run = async () => {
    setBusy(true);
    try {
      await rig.runProgram(tree, { cancel: busyProgram !== null });
      setError(null);
      onRan?.();
      onClose();
    } catch (e) {
      setError(e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const steps = tree.steps.length;
  const ready = steps > 0 && check?.ok === true && canOperate;
  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())} maxWidth="md" fullWidth data-testid="run-step-dialog">
      <DialogTitle>Run a step</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          Pick a step, fill it in, and run it now. Nothing is saved to the library; it shows on the program chip and in Events as “{ONE_OFF}”.
        </Typography>
        {busyProgram !== null && (
          <Alert severity="warning" sx={{ mb: 1.5 }} data-testid="run-step-busy">
            {busyProgram} is running. Running this cancels it first.
          </Alert>
        )}
        <ProgramBuilder
          tree={tree}
          onChange={setTree}
          programSchema={programSchema.data}
          controllers={controllers.data}
          devices={devices}
          stepErrors={check?.stepErrors ?? {}}
          stepWarnings={check?.stepWarnings ?? {}}
          revision={0}
          hideDescription
        />
        {check?.error && (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {check.error}
          </Alert>
        )}
        {error && (
          <Alert severity="error" sx={{ mt: 1.5 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Close
        </Button>
        <Button variant="contained" startIcon={<PlayArrowIcon />} onClick={() => void run()} disabled={busy || !ready} data-testid="run-step-go">
          {busyProgram !== null ? "Cancel it and run" : steps > 1 ? `Run ${steps} steps` : "Run"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
