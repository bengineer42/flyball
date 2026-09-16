import { Typography } from "@mui/material";

/**
 * What a widget shows when the thing it names is not on this rig (DESIGN-SPEC.md §2, §4.8):
 * the tile stays -- dashed border from `.fb-tile-missing`, never silently blank -- and says
 * what is missing. `name` empty means nothing was ever configured (a different row in the
 * spec's state table); given, it means this specific signal/controller/device is the
 * one the dashboard names but the rig does not have. Reconfigure (Rebind) or remove it from the
 * widget's own "⋯" menu in edit mode -- the same menu every widget has, so there is no second
 * set of controls to learn. `failed`: the name is an error message from a widget that threw, not a binding.
 */
export function Missing({ what, name, hint, failed = false }: { what: string; name: string; hint?: string; failed?: boolean }) {
  return (
    <div className="fb-tile-missing" role="note">
      <Typography variant="body2">{name ? <code>{name}</code> : `no ${what} configured`}</Typography>
      {name && !failed && (
        <Typography variant="caption" color="text.secondary">
          is not on this rig
        </Typography>
      )}
      {hint && (
        <Typography variant="caption" color="text.secondary">
          {hint}
        </Typography>
      )}
    </div>
  );
}
