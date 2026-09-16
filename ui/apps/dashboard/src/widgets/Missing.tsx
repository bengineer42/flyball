import { Typography } from "@mui/material";

/** What a widget shows when the thing it names is not on this rig: the tile stays, says what is missing, and can be reconfigured. */
export function Missing({ what, name, hint }: { what: string; name: string; hint?: string }) {
  return (
    <div className="fb-tile-missing" role="note">
      <Typography variant="body2">
        missing {what}: <code>{name || "—"}</code>
      </Typography>
      {hint && (
        <Typography variant="caption" color="text.secondary">
          {hint}
        </Typography>
      )}
    </div>
  );
}
