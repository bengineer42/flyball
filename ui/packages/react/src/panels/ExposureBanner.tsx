import type { Exposure } from "@flyball/client";

export interface ExposureBannerProps {
  /** `exposure` from `GET /api/auth`; nothing renders for null or a runner served where it was asked. */
  exposure: Exposure | null | undefined;
}

/**
 * A permanent banner for a runner whose exposure is not what it should be: open to the network by choice
 * (`--insecure-open`), or moved to loopback because it has no password and no token. No close button: it
 * stays while the condition does. Pure: the app decides where it goes (above every page).
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
              ? "This rig has no password and no token, and is open to anyone on the network: whoever reaches it may operate it."
              : "This rig has no password and no token, so it is served to this machine only."}
          </strong>
          {exposure.warning && <span className="fb-muted"> {exposure.warning}</span>}
        </div>
      </div>
    </div>
  );
}
