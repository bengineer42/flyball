import { useEffect, useState } from "react";
import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle, TextField } from "@mui/material";
import type { ConfirmLevel } from "./confirmLevels.js";

/**
 * A yes/no dialog; `danger` colours the action red. At `level` 2 the action stays off until the
 * person types `phrase` (the thing's name); level 1 (the default) is the plain popup. A caller
 * whose action is level 0 does not open it at all.
 */
export function Confirm({
  open,
  title,
  text,
  action,
  busy = false,
  onClose,
  onConfirm,
  danger = true,
  level = 1,
  phrase,
  children,
}: {
  open: boolean;
  title: string;
  text: string;
  action: string;
  busy?: boolean;
  onClose(): void;
  onConfirm(): void;
  danger?: boolean;
  level?: ConfirmLevel;
  phrase?: string;
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = useState("");
  useEffect(() => {
    if (!open) setTyped("");
  }, [open]);
  const mustType = level >= 2 && Boolean(phrase);
  const ready = !mustType || typed.trim() === phrase;
  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())}>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <DialogContentText>{text}</DialogContentText>
        {children}
        {mustType && (
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label={`Type ${phrase} to confirm`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && ready && !busy) onConfirm();
            }}
            inputProps={{ "aria-label": `type ${phrase} to confirm`, "data-testid": "confirm-phrase" }}
          />
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button color={danger ? "error" : "primary"} variant="contained" onClick={onConfirm} disabled={busy || !ready}>
          {action}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
