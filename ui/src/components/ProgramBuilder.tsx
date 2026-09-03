/**
 * Building a run.
 *
 * The list is the program: order is execution order, and the blocking steps
 * (ramp, hold) are marked so it is clear where the run will sit and wait. The
 * add palette is generated from the registry, grouped the way the commands are
 * grouped in `humctrl.cmds`.
 */

import { useState } from "react";

import type { Program, PumpsSpec, Step, StepKind } from "../api/types";
import { download, fromFile, ProgramParseError, toFile } from "../programs/library";
import { GROUP_LABELS, newId, STEP_DESCRIPTORS, STEP_ORDER, type StepGroup } from "../programs/steps";
import { issuesFor, type Issue } from "../programs/validate";
import { Card, Note, TextField } from "./primitives";
import { StepCard } from "./StepEditor";

export interface ProgramBuilderProps {
  program: Program;
  issues: ReadonlyArray<Issue>;
  spec: PumpsSpec | null | undefined;
  /** The step the rig is on, when this program is the one running. */
  currentStep: number | null;
  onChange(change: (program: Program) => Program): void;
  onImport(program: Program): void;
}

export function ProgramBuilder({
  program,
  issues,
  spec,
  currentStep,
  onChange,
  onImport,
}: ProgramBuilderProps) {
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const setSteps = (steps: Step[]) => onChange((current) => ({ ...current, steps }));

  const add = (kind: StepKind) => {
    const step = STEP_DESCRIPTORS[kind].create() as Step;
    setSteps([...program.steps, step]);
    setOpen(step.id);
    setAdding(false);
  };

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= program.steps.length) return;
    const steps = program.steps.slice();
    [steps[index], steps[target]] = [steps[target], steps[index]];
    setSteps(steps);
  };

  const groups = STEP_ORDER.reduce<Record<StepGroup, StepKind[]>>(
    (accumulator, kind) => {
      accumulator[STEP_DESCRIPTORS[kind].group].push(kind);
      return accumulator;
    },
    { control: [], pumps: [], recording: [] },
  );

  return (
    <Card
      title="Program"
      hint={`${program.steps.length} step${program.steps.length === 1 ? "" : "s"}`}
      actions={
        <>
          <button
            type="button"
            className="ghost small"
            onClick={() => download(`${program.name.replace(/\W+/g, "-").toLowerCase()}.json`, toFile(program))}
          >
            Export
          </button>
          <label className="button small" style={{ cursor: "pointer" }}>
            Import
            <input
              type="file"
              accept="application/json,.json"
              className="sr-only"
              onChange={async (event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (!file) return;
                try {
                  onImport(fromFile(await file.text()));
                  setImportError(null);
                } catch (error) {
                  setImportError(
                    error instanceof ProgramParseError ? error.message : "Could not read that file.",
                  );
                }
              }}
            />
          </label>
        </>
      }
    >
      <div className="stack tight">
        {importError && <Note tone="critical">{importError}</Note>}

        <div className="grid cols-2" style={{ gap: 8 }}>
          <TextField
            label="Name"
            value={program.name}
            onChange={(name) => onChange((current) => ({ ...current, name }))}
          />
          <TextField
            label="Record under flag"
            optional
            value={program.record_as ?? ""}
            placeholder="starts recording when the program starts"
            onChange={(value) => onChange((current) => ({ ...current, record_as: value || null }))}
          />
        </div>
        <TextField
          label="Description"
          optional
          value={program.description ?? ""}
          onChange={(description) => onChange((current) => ({ ...current, description }))}
        />

        <div className="list" style={{ marginTop: 8 }}>
          {program.steps.length === 0 && (
            <p className="hint" style={{ margin: 0 }}>
              No steps yet. Add one below — a run usually starts by starting a controller.
            </p>
          )}
          {program.steps.map((step, index) => (
            <StepCard
              key={step.id}
              index={index}
              step={step}
              open={open === step.id}
              current={currentStep === index}
              flowUnits={spec?.units}
              spec={spec}
              issues={issuesFor(issues as Issue[], step.id)}
              onToggle={() => setOpen(open === step.id ? null : step.id)}
              onChange={(next) =>
                setSteps(program.steps.map((each) => (each.id === step.id ? next : each)))
              }
              onMove={(delta) => move(index, delta)}
              onRemove={() => setSteps(program.steps.filter((each) => each.id !== step.id))}
              onDuplicate={() => {
                const copy = { ...step, id: newId() } as Step;
                const steps = program.steps.slice();
                steps.splice(index + 1, 0, copy);
                setSteps(steps);
              }}
            />
          ))}
        </div>

        {adding ? (
          <fieldset>
            <legend>Add a step</legend>
            <div className="stack tight">
              {(Object.keys(groups) as StepGroup[]).map((group) => (
                <div key={group}>
                  <h3 style={{ marginBottom: 6 }}>{GROUP_LABELS[group]}</h3>
                  <div className="palette">
                    {groups[group].map((kind) => {
                      const descriptor = STEP_DESCRIPTORS[kind];
                      return (
                        <button key={kind} type="button" onClick={() => add(kind)}>
                          <strong>{descriptor.label}</strong>
                          <span className="blurb">{descriptor.blurb}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
              <button type="button" className="ghost small" onClick={() => setAdding(false)}>
                Cancel
              </button>
            </div>
          </fieldset>
        ) : (
          <button type="button" onClick={() => setAdding(true)}>
            Add step
          </button>
        )}
      </div>
    </Card>
  );
}
