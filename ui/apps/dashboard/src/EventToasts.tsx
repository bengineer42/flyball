import { Alert, IconButton, Snackbar } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { describeEventKind, describeSubject, type RigEvent } from "@flyball/client";

/** ERROR toasts stay red; WARNING (the lowest `useUnreadEvents` toasts) reads as a caution, not a failure. */
const severity = (level: RigEvent["level"]) => (level === "ERROR" ? "error" : "warning");

/**
 * One WARNING+ event at a time, wherever the viewer is in the app: mounted once at the
 * app root and fed by the same `useUnreadEvents` queue the nav badge counts. Dismissing
 * (click, close button or the auto-hide timeout) marks that event read and shows the next.
 */
export function EventToasts({ toasts, onDismiss }: { toasts: RigEvent[]; onDismiss(e: RigEvent): void }) {
  const current = toasts[0];
  return (
    <Snackbar
      open={current !== undefined}
      autoHideDuration={8000}
      onClose={() => current && onDismiss(current)}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      data-testid="event-toast"
    >
      {current && (
        <Alert
          severity={severity(current.level)}
          variant="filled"
          sx={{ maxWidth: 420 }}
          action={
            <IconButton
              size="small"
              color="inherit"
              aria-label="dismiss"
              data-testid="event-toast-dismiss"
              onClick={() => onDismiss(current)}
            >
              <CloseIcon fontSize="small" />
            </IconButton>
          }
        >
          {describeEventKind(current.kind)} — {describeSubject(current.subject)}: {current.message}
        </Alert>
      )}
    </Snackbar>
  );
}
