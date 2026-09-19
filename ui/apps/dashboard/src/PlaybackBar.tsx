import { memo } from "react";
import { Box, IconButton, Slider, Tooltip, Typography, alpha } from "@mui/material";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import PauseIcon from "@mui/icons-material/Pause";
import FastRewindIcon from "@mui/icons-material/FastRewind";
import FastForwardIcon from "@mui/icons-material/FastForward";
import { PLAYBACK_STEP_S, type PlaybackHook } from "@flyball/react";

function hms(s: number): string {
  const d = new Date(s * 1000);
  return d.toLocaleTimeString([], { hour12: false });
}

/**
 * A video-style transport for a simulated rig's own recorded history:
 * play/pause, rewind, fast-forward, and a scrub slider from the session's
 * start to now. Paused (or scrubbed back), the bar reports `atS`/`paused`
 * and `usePlayback` puts the telemetry store into playback, so every panel
 * that reads samples shows the rig as it was then; the bar itself is the
 * only sign -- an amber stamp and a faint tint, no per-panel badge (the
 * spec's quiet-by-default rule; nothing moves). Nothing here writes a
 * demand or a setpoint, so it is read-only by construction; resuming (the
 * play button, or fast-forwarding past `nowS`) goes straight back to live.
 * Shown only for simulated rigs (`Shell`'s `simulated` prop already gates
 * the page it sits on the same way).
 */
export const PlaybackBar = memo(function PlaybackBar({ playback }: { playback: PlaybackHook }) {
  const { paused, atS, startS, nowS, rewind, fastForward, pause, resume, seek } = playback;
  if (startS === undefined) return null;
  const range = Math.max(1, nowS - startS);
  return (
    <Box
      data-playback={paused ? "paused" : "live"}
      sx={{ display: "flex", alignItems: "center", gap: 1, px: 2, py: 0.25, borderBottom: 1, borderColor: "divider", bgcolor: paused ? (t) => alpha(t.palette.warning.main, 0.08) : undefined }}
    >
      <Tooltip title={`Rewind ${PLAYBACK_STEP_S}s`}>
        <span>
          <IconButton size="small" aria-label="rewind" onClick={() => rewind()} disabled={atS <= startS}>
            <FastRewindIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Tooltip title={paused ? "Resume live" : "Pause"}>
        <IconButton size="small" aria-label={paused ? "resume" : "pause"} onClick={() => (paused ? resume() : pause())}>
          {paused ? <PlayArrowIcon fontSize="small" /> : <PauseIcon fontSize="small" />}
        </IconButton>
      </Tooltip>
      <Tooltip title={`Fast-forward ${PLAYBACK_STEP_S}s`}>
        <span>
          <IconButton size="small" aria-label="fast-forward" onClick={() => fastForward()} disabled={!paused}>
            <FastForwardIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Slider
        size="small"
        aria-label="scrub"
        min={startS}
        max={nowS}
        step={1}
        value={atS}
        onChange={(_e, v) => seek(v as number)}
        sx={{ mx: 1, flex: "1 1 auto", minWidth: 120 }}
      />
      <Typography variant="caption" color={paused ? "warning.main" : "text.secondary"} sx={{ whiteSpace: "nowrap", minWidth: "9ch", textAlign: "right" }}>
        {paused ? `${hms(atS)} · read-only` : "live"}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: { xs: "none", sm: "block" } }}>
        {hms(startS)}–{hms(nowS)}
      </Typography>
    </Box>
  );
});
