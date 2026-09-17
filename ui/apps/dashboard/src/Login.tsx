import { useState, type FormEvent } from "react";
import { Alert, Box, Button, Chip, Divider, Menu, MenuItem, Paper, Stack, TextField, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import LockOpenOutlinedIcon from "@mui/icons-material/LockOpenOutlined";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import KeyOutlinedIcon from "@mui/icons-material/KeyOutlined";
import { RigError } from "@flyball/client";
import { useAuth } from "./auth.js";
import { PasskeyManager } from "./PasskeyManager.js";

/** Whether this browser, in this context, can even attempt a WebAuthn ceremony. */
const passkeysSupported = () => typeof window !== "undefined" && "PublicKeyCredential" in window;

/**
 * The login page: one password field. Replaces the whole app while the runner says this browser may see
 * nothing (`level: none`), and stands in for a page whose request came back 401 after a session ended.
 * A runner with only a token takes that here too -- the browser trades it for a cookie and keeps nothing.
 */
export function LoginPage({ onCancel }: { onCancel?: () => void } = {}) {
  const { login, loginWithPasskey, info } = useAuth();
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = info?.password ? "Password" : "Token";

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

  const submitPasskey = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await loginWithPasskey();
    } catch (err) {
      if (err instanceof RigError && err.status === 401) setError("That passkey did not verify");
      else if (err instanceof RigError && err.status === 429) setError("Too many tries; wait a minute");
      else if (err instanceof Error && err.name === "NotAllowedError") {
        /* cancelled or timed out in the browser's own UI; nothing to say */
      } else setError(err instanceof Error ? err.message : String(err));
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
            {info?.password
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
          {passkeysSupported() && (
            <>
              <Divider>or</Divider>
              <Button
                variant="outlined"
                startIcon={<KeyOutlinedIcon />}
                disabled={busy}
                onClick={submitPasskey}
                data-testid="login-passkey"
              >
                Sign in with a passkey
              </Button>
            </>
          )}
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
  const { open, signedIn, canOperate, logout, info } = useAuth();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [managingPasskeys, setManagingPasskeys] = useState(false);
  const narrow = useMediaQuery(useTheme().breakpoints.down("sm"));
  if (open || info === null) return null;
  const compact = narrow ? { "& .MuiChip-label": { display: "none" }, "& .MuiChip-icon": { m: 0 } } : undefined;

  if (signedIn) {
    return (
      <>
        <Tooltip title="Signed in to this rig">
          <Chip variant="outlined" icon={<LockOpenOutlinedIcon fontSize="small" />} label={narrow ? "" : "signed in"} sx={compact} onClick={(e) => setAnchor(e.currentTarget)} data-testid="auth-chip" />
        </Tooltip>
        <Menu open={anchor !== null} anchorEl={anchor} onClose={() => setAnchor(null)}>
          {passkeysSupported() && (
            <MenuItem
              onClick={() => {
                setAnchor(null);
                setManagingPasskeys(true);
              }}
              data-testid="auth-manage-passkeys"
            >
              Manage passkeys
            </MenuItem>
          )}
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
        <PasskeyManager open={managingPasskeys} onClose={() => setManagingPasskeys(false)} />
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
