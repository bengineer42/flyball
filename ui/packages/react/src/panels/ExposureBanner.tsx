import type { Exposure } from "@flyball/client";

export interface ExposureBannerProps {
  /** `exposure` from `GET /api/auth`; nothing renders for null or a runner served where it was asked. */
  exposure: Exposure | null | undefined;
}

/**
 * A permanent banner for a rig whose exposure is not what it should be: open to the network by choice
 * (`--insecure-open`), or moved to loopback because it asks nobody to sign in -- a bare runner with no token,
 * or a front whose configured sign-in could not be used and fell back to the `local` shape. Which of those
 * it is, `warning` says in the server's words; the headline claims only what `exposure` shows. No close
 * button: it stays while the condition does. Pure: the app decides where it goes (above every page).
 */
export function ExposureBanner({ exposure }: ExposureBannerProps) {
  if (!exposure || !(exposure.open_network || exposure.restricted)) return null;
  const open = exposure.open_network;
  return (
    <div className="fb-signals" role="alert" data-testid="exposure-banner">
      <div className="fb-signal" style={{ borderColor: open ? "var(--fb-alarm)" : "var(--fb-warn)", background: open ? "var(--fb-alarm-fill)" : "var(--fb-warn-fill)" }}>
        <span className="fb-signal-icon" aria-hidden="true">
          ⚠
        </span>
        <div className="fb-signal-text">
          <strong>
            {open
              ? "This rig asks nobody to sign in, and is open to anyone on the network: whoever reaches it may operate it."
              : exposure.open
                ? "This rig asks nobody to sign in, so it is served to this machine only."
                : `This rig is served on ${exposure.host}, not where it was asked (${exposure.requested_host}).`}
          </strong>
          {exposure.warning && <span className="fb-muted"> {exposure.warning}</span>}
        </div>
      </div>
    </div>
  );
}
