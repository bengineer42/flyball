import { useState } from "react";
import { Alert, Button, Stack } from "@mui/material";
import { RigError, type Health, type LatchRow } from "@flyball/client";
import { useHealth, useRig } from "@flyball/react";
import { Confirm } from "./Confirm.js";
import { confirmLevel } from "./confirmLevels.js";
import { useAuth } from "./auth.js";

/** How often the page asks whether the rig is latched: a stop from elsewhere shows within this. */
const LATCH_POLL_MS = 3000;

/** The latch causes held, each with the subjects it holds: `stop` first, then each `on_fault:<controller>`. */
export function latchCauses(latches: LatchRow[]): { cause: string; subjects: string[] }[] {
  const by = new Map<string, string[]>();
  for (const row of latches) by.set(row.cause, [...(by.get(row.cause) ?? []), row.subject]);
  return [...by.entries()].sort(([a], [b]) => (a === "stop" ? -1 : b === "stop" ? 1 : a.localeCompare(b))).map(([cause, subjects]) => ({ cause, subjects }));
}

/** A latch cause as a person reads it: the rig stop, or a controller's fault action. */
export function describeCause(cause: string): string {
  if (cause === "stop") return "the software stop";
  if (cause.startsWith("on_fault:")) return `${cause.slice("on_fault:".length)}'s fault action`;
  return cause;
}

const when = (ns: number) => new Date(ns / 1e6).toLocaleTimeString();

/**
 * Says, on every page, that the rig is latched and by what, until a person resets it: the software
 * stop (who, when, why) and each controller's fault action that latched. Reset is `operate` and a
 * person; the rig refuses it from a service token or a model. Renders nothing when nothing is latched.
 */
export function LatchBanner() {
  const health = useHealth(LATCH_POLL_MS);
  return <LatchBannerFor health={health.data} onReset={health.refresh} />;
}

export function LatchBannerFor({ health, onReset }: { health: Health | undefined; onReset(): void }) {
  const { canOperate } = useAuth();
  const rig = useRig();
  const [resetting, setResetting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!health) return null;
  const causes = latchCauses(health.latches ?? []);
  const stopped = health.stopped;
  if (!stopped && causes.length === 0) return null;

  const reset = async (cause: string) => {
    setBusy(true);
    setError(null);
    try {
      await rig.resetRig(cause);
      setResetting(null);
      onReset();
    } catch (e) {
      setError(e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const ask = (cause: string) => (confirmLevel("rig.reset") === 0 ? void reset(cause) : setResetting(cause));

  const rows = causes.length ? causes : [{ cause: "stop", subjects: [] }];
  return (
    <Stack spacing={1} sx={{ mb: 2 }} data-testid="latch-banner">
      {rows.map(({ cause, subjects }) => (
        <Alert
          key={cause}
          severity={cause === "stop" ? "error" : "warning"}
          action={
            <Button color="inherit" size="small" variant="outlined" disabled={!canOperate || busy} onClick={() => ask(cause)} data-testid={`reset-${cause}`}>
              Reset
            </Button>
          }
        >
          {cause === "stop" && stopped
            ? `Rig stopped by ${stopped.actor.principal} at ${when(stopped.at_utc_ns)}${stopped.reason ? `: ${stopped.reason}` : ""}. `
            : `Latched by ${describeCause(cause)}${subjects.length ? ` (${subjects.join(", ")})` : ""}. `}
          Regulation and automatic writes stay refused until a person resets it.
          {!canOperate && " Sign in to operate to reset."}
        </Alert>
      ))}
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <Confirm
        open={resetting !== null}
        title={resetting === "stop" ? "Reset the software stop?" : `Reset ${resetting ? describeCause(resetting) : ""}?`}
        text={
          resetting === "stop"
            ? "Lets the latch go. Nothing resumes by itself and the reset writes nothing: controllers stay in manual until someone sets them regulating, and programs and automatic writes are allowed again."
            : "Lets this fault latch go and clears the controller's law state. It stays in manual until someone sets it regulating."
        }
        action="Reset"
        danger={false}
        level={confirmLevel("rig.reset")}
        busy={busy}
        onClose={() => setResetting(null)}
        onConfirm={() => resetting && void reset(resetting)}
      />
    </Stack>
  );
}
