/**
 * The saved programs.
 *
 * Local to this browser until the daemon can hold them (R1), which the panel
 * says outright so nobody assumes a program written here is on the rig.
 */

import type { Program } from "../api/types";
import type { ProgramLibrary } from "../state/usePrograms";
import { Card, Note } from "./primitives";

export interface ProgramListProps {
  library: ProgramLibrary;
  /** True once the daemon can store programs itself. */
  serverSide: boolean;
}

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ProgramList({ library, serverSide }: ProgramListProps) {
  return (
    <Card
      title="Programs"
      actions={
        <button type="button" className="small" onClick={() => library.create()}>
          New
        </button>
      }
    >
      <div className="stack tight">
        {!serverSide && (
          <Note>
            Saved in this browser only. Export anything you want to keep — the daemon cannot store
            programs yet (R1).
          </Note>
        )}
        <div className="list">
          {library.programs.map((program: Program) => (
            <button
              key={program.id}
              type="button"
              className="list-item"
              aria-selected={program.id === library.selectedId}
              onClick={() => library.select(program.id)}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{program.name}</div>
                <div className="meta">
                  {program.steps.length} step{program.steps.length === 1 ? "" : "s"} ·{" "}
                  {when(program.modified)}
                </div>
              </div>
              <span
                className="row"
                onClick={(event) => {
                  event.stopPropagation();
                }}
              >
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => library.duplicate(program.id)}
                  aria-label={`Duplicate ${program.name}`}
                >
                  ⧉
                </button>
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => {
                    if (window.confirm(`Delete "${program.name}"?`)) library.remove(program.id);
                  }}
                  aria-label={`Delete ${program.name}`}
                >
                  ✕
                </button>
              </span>
            </button>
          ))}
        </div>
      </div>
    </Card>
  );
}
