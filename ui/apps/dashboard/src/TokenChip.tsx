import { useRef, useState } from "react";
import { Box, Button, Chip, ClickAwayListener, Paper, Popper, Stack, TextField, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import VpnKeyIcon from "@mui/icons-material/VpnKeyOutlined";
import { useToken } from "./token.js";

/**
 * A small, always-present chrome entry for the daemon's bearer token (app bar, beside the other status
 * chips): quiet when nothing needs it, a place to paste or change one either way. The *prominent* case --
 * a 401 on the very first request -- is `TokenPrompt` below, which replaces the whole page instead.
 */
export function TokenChip() {
  const { token, setToken } = useToken();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(token);
  const anchorRef = useRef<HTMLDivElement | null>(null);

  const openPopover = () => {
    setDraft(token);
    setOpen(true);
  };
  const save = () => {
    setToken(draft.trim());
    setOpen(false);
  };

  const narrow = useMediaQuery(useTheme().breakpoints.down("sm"));
  return (
    <>
      <Tooltip title={token ? "Bearer token set for this rig" : "No token set (the rig is open)"}>
        <Chip
          ref={anchorRef}
          variant="outlined"
          color="default"
          icon={<VpnKeyIcon fontSize="small" />}
          label={narrow ? "" : "token"}
          sx={narrow ? { "& .MuiChip-label": { display: "none" }, "& .MuiChip-icon": { m: 0 } } : undefined}
          onClick={openPopover}
          data-testid="token-chip"
        />
      </Tooltip>
      <Popper open={open} anchorEl={anchorRef.current} placement="bottom-end" style={{ zIndex: 1300 }}>
        <ClickAwayListener onClickAway={() => setOpen(false)}>
          <Paper sx={{ p: 2, width: 320 }} elevation={4}>
            <Stack spacing={1.5}>
              <Typography variant="body2" color="text.secondary">
                Bearer token for a daemon started with <code>--token</code>. Kept in this browser only.
              </Typography>
              <TextField
                size="small"
                label="token"
                type="password"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoFocus
                fullWidth
                inputProps={{ "data-testid": "token-input" }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") save();
                }}
              />
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button size="small" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
                <Button size="small" variant="contained" onClick={save} data-testid="token-save">
                  Save
                </Button>
              </Stack>
            </Stack>
          </Paper>
        </ClickAwayListener>
      </Popper>
    </>
  );
}

/**
 * Replaces the whole page when the first thing the app asks the rig (`GET /api/devices`) comes back 401:
 * nothing else can be shown or done until a token is given. `App` renders this instead of the usual
 * "cannot reach the rig" alert for exactly this status.
 */
export function TokenPrompt() {
  const { token, setToken } = useToken();
  const [draft, setDraft] = useState(token);

  return (
    <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", p: 3 }}>
      <Paper sx={{ p: 4, maxWidth: 420, width: "100%" }} elevation={2}>
        <Stack spacing={2}>
          <Typography variant="h2" component="h1">
            This rig needs a token
          </Typography>
          <Typography variant="body2" color="text.secondary">
            The daemon was started with <code>--token</code>: every request needs it. Paste it below, or open
            this page again with <code>?token=…</code> in the URL.
          </Typography>
          <TextField
            size="small"
            label="token"
            type="password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
            fullWidth
            inputProps={{ "data-testid": "token-prompt-input" }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && draft.trim()) setToken(draft.trim());
            }}
          />
          <Button variant="contained" disabled={!draft.trim()} onClick={() => setToken(draft.trim())} data-testid="token-prompt-save">
            Connect
          </Button>
        </Stack>
      </Paper>
    </Box>
  );
}
