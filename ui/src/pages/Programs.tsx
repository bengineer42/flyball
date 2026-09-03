/**
 * Writing and running a program.
 *
 * Validation runs on every edit against the rig's real limits, so a flow past
 * the pumps is caught while typing rather than at step 7 with the pumps
 * already going.
 */

import { useMemo } from "react";

import type { Program, RigState } from "../api/types";
import { validateProgram } from "../programs/validate";
import type { ProgramLibrary } from "../state/usePrograms";
import type { RigHandle } from "../state/useRig";
import { ProgramBuilder } from "../components/ProgramBuilder";
import { ProgramList } from "../components/ProgramList";
import { ProgramRunner } from "../components/ProgramRunner";
import { Card } from "../components/primitives";

export interface ProgramsProps {
  rig: RigHandle;
  state: RigState | null;
  library: ProgramLibrary;
  confirm: boolean;
}

export function Programs({ rig, state, library, confirm }: ProgramsProps) {
  const program: Program | null = library.selected;
  const spec = state?.pumps_spec ?? null;

  const issues = useMemo(
    () =>
      program
        ? validateProgram(program, { pumpsSpec: spec, capabilities: rig.capabilities })
        : [],
    [program, spec, rig.capabilities],
  );

  const currentStep =
    state?.program?.running && state.program.program_id === program?.id
      ? state.program.step
      : null;

  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(240px, 320px) 1fr", alignItems: "start" }}>
      <div className="stack">
        <ProgramList library={library} serverSide={rig.capabilities.programs && rig.transport.kind === "live"} />
        {program && (
          <ProgramRunner
            rig={rig}
            state={state}
            program={program}
            issues={issues}
            confirm={confirm}
          />
        )}
      </div>

      {program ? (
        <ProgramBuilder
          program={program}
          issues={issues}
          spec={spec}
          currentStep={currentStep}
          onChange={(change) => library.update(program.id, change)}
          onImport={library.add}
        />
      ) : (
        <Card title="Program">
          <p className="hint" style={{ margin: 0 }}>
            No program selected. Create one on the left.
          </p>
        </Card>
      )}
    </div>
  );
}
