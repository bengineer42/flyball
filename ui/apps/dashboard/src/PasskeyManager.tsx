import { useEffect, useState } from "react";
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, List, ListItem, ListItemSecondaryAction, ListItemText, TextField, Typography } from "@mui/material";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import type { PasskeyOut } from "@flyball/client";
import { useAuth } from "./auth.js";

/**
 * "Manage passkeys": reached from the signed-in `<AuthChip>` menu. Lists what is registered,
 * registers a new one (a label, then the browser's own ceremony), revokes any of them.
 */
export function PasskeyManager({ open, onClose }: { open: boolean; onClose(): void }) {
  const { listPasskeys, registerPasskey, deletePasskey } = useAuth();
  const [passkeys, setPasskeys] = useState<PasskeyOut[] | null>(null);
  const [storeBacked, setStoreBacked] = useState(true);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    listPasskeys()
      .then((listing) => {
        setPasskeys(listing.passkeys);
        setStoreBacked(listing.store_backed);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  useEffect(() => {
    if (open) load();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const register = async () => {
    if (!label || busy) return;
    setBusy(true);
    setError(null);
    try {
      await registerPasskey(label);
      setLabel("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: number) => {
    setBusy(true);
    setError(null);
    try {
      await deletePasskey(id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth data-testid="passkey-manager">
      <DialogTitle>Passkeys</DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} data-testid="passkey-error">
            {error}
          </Alert>
        )}
        <List dense data-testid="passkey-list">
          {(passkeys ?? []).map((p) => (
            <ListItem key={p.id} data-testid={`passkey-${p.id}`}>
              <ListItemText primary={p.label} secondary={new Date(p.created_ns / 1e6).toLocaleString()} />
              <ListItemSecondaryAction>
                <IconButton edge="end" aria-label="revoke" disabled={busy} onClick={() => revoke(p.id)} data-testid={`passkey-revoke-${p.id}`}>
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </ListItemSecondaryAction>
            </ListItem>
          ))}
          {passkeys?.length === 0 && (
            <Typography variant="body2" color="text.secondary" sx={{ px: 2, py: 1 }}>
              No passkeys registered yet.
            </Typography>
          )}
        </List>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", mt: 2 }}>
          <TextField
            size="small"
            label="Label"
            placeholder="e.g. Ben's laptop"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            fullWidth
            inputProps={{ "data-testid": "passkey-label" }}
          />
          <Button variant="contained" disabled={!label || busy} onClick={register} data-testid="passkey-register">
            Register
          </Button>
        </Box>
        {!storeBacked && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }} data-testid="passkey-store-warning">
            Note: this runner has no persistent store; passkeys won't survive a restart.
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} data-testid="passkey-manager-close">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}
