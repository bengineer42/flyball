import { useState, type FormEvent } from "react";
import { Alert, Box, Button, Chip, Menu, MenuItem, Paper, Stack, TextField, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import LockOpenOutlinedIcon from "@mui/icons-material/LockOpenOutlined";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import { RigError } from "@flyball/client";
import { useAuth } from "./auth.js";

/**
 * The login page: one password field. Replaces the whole app while the runner says this browser may see
 * nothing (`level: none`), and stands in for a page whose request came back 401 after a session ended.
 * A runner with only a token takes that here too -- the browser trades it for a cookie and keeps nothing.
 */
export function LoginPage({ onCancel }: { onCancel?: () => void } = {}) {
  const { login, info } = useAuth();
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = info?.login.password ? "Password" : "Token";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!secret || busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(secret);
    } catch (err) {
      if (err instanceof RigError && err.status === 401) setError(`Wrong ${label.toLowerCase()}`);
      else if (err instanceof RigError && err.status === 429) setError("Too many tries; wait a minute");
      else setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", p: 3 }}>
      <Paper component="form" onSubmit={submit} sx={{ p: 4, maxWidth: 400, width: "100%" }} elevation={2} data-testid="login">
        <Stack spacing={2}>
          <Typography variant="h2" component="h1">
            Sign in
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {info?.login.password
              ? "This rig needs a password."
              : "This rig needs its token: the one the runner was started with (--token)."}
          </Typography>
          <TextField
            size="small"
            label={label}
            type="password"
            autoComplete="current-password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            autoFocus
            fullWidth
            error={error !== null}
            inputProps={{ "data-testid": "login-secret" }}
          />
          {error && (
            <Alert severity="error" data-testid="login-error">
              {error}
            </Alert>
          )}
          {/* Phase 3: a passkey button belongs here, guarded by `info?.login.passkey` (always false
              in Phase 1). Left as its own row so it slots in beside the password/token field
              without reshaping this form. */}
          <Stack direction="row" spacing={1} justifyContent="flex-end">
            {onCancel && (
              <Button onClick={onCancel} data-testid="login-cancel">
                Keep looking
              </Button>
            )}
            <Button type="submit" variant="contained" disabled={!secret || busy} data-testid="login-submit">
              Sign in
            </Button>
          </Stack>
        </Stack>
      </Paper>
    </Box>
  );
}

/**
 * The app bar's door: nothing on an open runner; "Signed in" with a sign-out menu after a login; on a runner
 * anyone may read, "Read only" with a way to the login page. Where the token chip used to be.
 */
export function AuthChip({ onSignIn }: { onSignIn(): void }) {
  const { open, signedIn, canOperate, versionMismatch, logout, info } = useAuth();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const narrow = useMediaQuery(useTheme().breakpoints.down("sm"));
  const compact = narrow ? { "& .MuiChip-label": { display: "none" }, "& .MuiChip-icon": { m: 0 } } : undefined;

  // Checked before everything else, including the open/local shape: a version disagreement means
  // this client cannot trust its reading of *any* other field on `info`, so it says so rather than
  // silently falling back to guessed behaviour.
  if (versionMismatch) {
    return (
      <Tooltip title="This runner's auth answer is a different version than this UI expects; reload, or update whichever is stale">
        <Chip variant="outlined" color="error" label={narrow ? "" : "version mismatch"} sx={compact} data-testid="auth-version-mismatch" />
      </Tooltip>
    );
  }
  if (open || info === null) return null;

  if (signedIn) {
    return (
      <>
        <Tooltip title="Signed in to this rig">
          <Chip variant="outlined" icon={<LockOpenOutlinedIcon fontSize="small" />} label={narrow ? "" : "signed in"} sx={compact} onClick={(e) => setAnchor(e.currentTarget)} data-testid="auth-chip" />
        </Tooltip>
        <Menu open={anchor !== null} anchorEl={anchor} onClose={() => setAnchor(null)}>
          <MenuItem
            onClick={() => {
              setAnchor(null);
              void logout();
            }}
            data-testid="auth-signout"
          >
            Sign out
          </MenuItem>
        </Menu>
      </>
    );
  }
  if (!canOperate) {
    return (
      <Tooltip title="Anyone may look at this rig; sign in to operate it">
        <Chip variant="outlined" color="warning" icon={<VisibilityOutlinedIcon fontSize="small" />} label={narrow ? "" : "read only"} sx={compact} onClick={onSignIn} data-testid="auth-chip" />
      </Tooltip>
    );
  }
  // A machine's token in a browser (a custom transport); nothing to sign out of.
  return (
    <Tooltip title="In with the runner's token">
      <Chip variant="outlined" icon={<LockOutlinedIcon fontSize="small" />} label={narrow ? "" : "token"} sx={compact} data-testid="auth-chip" />
    </Tooltip>
  );
}
