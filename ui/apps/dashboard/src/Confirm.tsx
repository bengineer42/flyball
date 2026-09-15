import { Button, Dialog, DialogActions, DialogContent, DialogContentText, DialogTitle } from "@mui/material";

/** A yes/no dialog; `danger` colours the action red. */
export function Confirm({ open, title, text, action, busy = false, onClose, onConfirm, danger = true }: { open: boolean; title: string; text: string; action: string; busy?: boolean; onClose(): void; onConfirm(): void; danger?: boolean }) {
  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())}>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <DialogContentText>{text}</DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button color={danger ? "error" : "primary"} variant="contained" onClick={onConfirm} disabled={busy}>
          {action}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
