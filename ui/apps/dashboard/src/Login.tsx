import { useState, type FormEvent } from "react";
import { Alert, Box, Button, Chip, Divider, Menu, MenuItem, Paper, Stack, TextField, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import LockOpenOutlinedIcon from "@mui/icons-material/LockOpenOutlined";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import KeyOutlinedIcon from "@mui/icons-material/KeyOutlined";
import { RigError } from "@flyball/client";
import { useAuth } from "./auth.js";
import { PasskeyManager } from "./PasskeyManager.js";

/** Whether this browser, in this context, can even attempt a WebAuthn ceremony. For Phase 3 (passkeys in the Go front). */
const passkeysSupported = () => typeof window !== "undefined" && "PublicKeyCredential" in window;

/**
 * The login page. Replaces the whole app while the door says this browser may see nothing, and stands in
 * for a page whose request came back 401 after a session ended. What it asks for is `info.login`'s: a
 * `password` front takes its password, a bare runner its token (traded for a cookie; the browser keeps
 * nothing). A `proxy` front offers neither -- the proxy in front of it signs people in -- so the page says
 * that and has no field.
 */
export function LoginPage({ onCancel }: { onCancel?: () => void } = {}) {
  const { login, loginWithPasskey, info } = useAuth();
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const label = info?.login.password ? "Password" : "Token";
  const typed = Boolean(info?.login.password || info?.login.token);

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
      <Paper component="form" onSubmit={submit} sx={{ p: 4, maxWidth: 400, width: "100%" }} elevation={2} data-testid="login" data-shape={info?.shape}>
        <Stack spacing={2}>
          <Typography variant="h2" component="h1">
            Sign in
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {info?.login.password
              ? "This rig needs a password."
              : info?.login.token
                ? "This rig needs its token: the one the runner was started with (--token)."
                : info?.shape === "proxy"
                  ? "This rig's sign-in is the proxy in front of it, and this request reached it without one. Open the rig through the proxy's own address, or sign in there, then reload."
                  : "This rig offers no sign-in here."}
          </Typography>
          {typed && (
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
          )}
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
            {typed && (
              <Button type="submit" variant="contained" disabled={!secret || busy} data-testid="login-submit">
                Sign in
              </Button>
            )}
          </Stack>
          {/* For Phase 3 (passkeys in the Go front): hidden while `info.login.passkey` is false, as the
              front always sends it in Phase 1. */}
          {info?.login.passkey && passkeysSupported() && (
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
  const { open, signedIn, canOperate, versionMismatch, logout, info } = useAuth();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [managingPasskeys, setManagingPasskeys] = useState(false);
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
          {info?.login.passkey && passkeysSupported() && (
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
        {info.login.passkey && <PasskeyManager open={managingPasskeys} onClose={() => setManagingPasskeys(false)} />}
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
