/**
 * Starting a program on the rig.
 *
 * Errors block the run outright; warnings are shown and the run is still
 * offered, because a program that only drives the pumps legitimately trips
 * several of them. Progress comes from the rig's own state, not from a timer
 * here — if the daemon is stepping the program, it is the one that knows where
 * it has got to.
 */

import type { Program, RigState } from "../api/types";
import type { RigHandle } from "../state/useRig";
import { summarise } from "../programs/steps";
import { hasErrors, type Issue } from "../programs/validate";
import { Badge, Card, Note } from "./primitives";

export interface ProgramRunnerProps {
  rig: RigHandle;
  state: RigState | null;
  program: Program;
  issues: ReadonlyArray<Issue>;
  confirm: boolean;
}

export function ProgramRunner({ rig, state, program, issues, confirm }: ProgramRunnerProps) {
  const status = state?.program ?? null;
  const running = Boolean(status?.running);
  const mine = status?.program_id === program.id;
  const blocked = hasErrors(issues as Issue[]);
  const warnings = issues.filter((issue) => issue.level === "warning").length;
  const busy = rig.command.pending !== null;

  return (
    <Card
      title="Run"
      hint={rig.capabilities.programs ? undefined : "not supported by this rig"}
      actions={
        running ? (
          <button
            type="button"
            className="danger small"
            disabled={busy}
            onClick={() => void rig.run("Interrupt", (transport) => transport.interruptProgram())}
          >
            Interrupt
          </button>
        ) : (
          <button
            type="button"
            className="primary small"
            disabled={busy || blocked || program.steps.length === 0}
            onClick={() => {
              if (
                confirm &&
                !window.confirm(
                  `Run "${program.name}" (${program.steps.length} steps)? This drives the rig.`,
                )
              ) {
                return;
              }
              void rig.run("Run program", (transport) => transport.runProgram(program));
            }}
          >
            Run program
          </button>
        )
      }
    >
      <div className="stack tight">
        {!rig.capabilities.programs && (
          <Note tone="warning">
            This daemon has no program endpoints (R1). Build and export here, or switch to the
            simulated rig in Settings to try the program end to end.
          </Note>
        )}

        {blocked && (
          <Note tone="critical">
            Fix the errors in the steps above before running.
          </Note>
        )}
        {!blocked && warnings > 0 && (
          <Note tone="warning">
            {warnings} warning{warnings > 1 ? "s" : ""} — the run is allowed, but read them first.
          </Note>
        )}

        {running && mine && status && (
          <div className="stack tight">
            <div className="row">
              <Badge tone="good" dot>
                step {status.step + 1} of {status.step_count}
              </Badge>
              <span className="hint">{summarise(program.steps[status.step] ?? program.steps[0])}</span>
            </div>
          </div>
        )}
        {running && !mine && status && (
          <Note tone="warning">
            The rig is running "{status.name}". Interrupt it before starting another.
          </Note>
        )}
      </div>
    </Card>
  );
}
