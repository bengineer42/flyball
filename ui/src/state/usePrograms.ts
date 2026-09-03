/**
 * The program library.
 *
 * CRUD over local storage, with the selected program held here so the builder
 * and the run panel agree on what is being edited.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { Program, Step } from "../api/types";
import { emptyProgram, exampleProgram, loadPrograms, savePrograms } from "../programs/library";

export interface ProgramLibrary {
  programs: Program[];
  selected: Program | null;
  selectedId: string | null;
  select(id: string | null): void;
  create(name?: string): Program;
  add(program: Program): void;
  update(id: string, change: (program: Program) => Program): void;
  remove(id: string): void;
  duplicate(id: string): void;
  setSteps(id: string, steps: Step[]): void;
}

export function usePrograms(): ProgramLibrary {
  const [programs, setPrograms] = useState<Program[]>(() => {
    const stored = loadPrograms();
    // First run gets a worked example rather than an empty list.
    return stored.length > 0 ? stored : [exampleProgram()];
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => savePrograms(programs), [programs]);

  useEffect(() => {
    if (selectedId === null && programs.length > 0) setSelectedId(programs[0].id);
    if (selectedId !== null && !programs.some((p) => p.id === selectedId)) {
      setSelectedId(programs[0]?.id ?? null);
    }
  }, [programs, selectedId]);

  const update = useCallback((id: string, change: (program: Program) => Program) => {
    setPrograms((current) =>
      current.map((program) =>
        program.id === id ? { ...change(program), modified: Date.now() / 1000 } : program,
      ),
    );
  }, []);

  const add = useCallback((program: Program) => {
    setPrograms((current) => [program, ...current]);
    setSelectedId(program.id);
  }, []);

  const create = useCallback(
    (name?: string) => {
      const program = emptyProgram(name);
      add(program);
      return program;
    },
    [add],
  );

  const selected = useMemo(
    () => programs.find((program) => program.id === selectedId) ?? null,
    [programs, selectedId],
  );

  return {
    programs,
    selected,
    selectedId,
    select: setSelectedId,
    create,
    add,
    update,
    remove: useCallback(
      (id: string) => setPrograms((current) => current.filter((program) => program.id !== id)),
      [],
    ),
    duplicate: useCallback(
      (id: string) =>
        setPrograms((current) => {
          const source = current.find((program) => program.id === id);
          if (!source) return current;
          const copy: Program = {
            ...source,
            id: `${source.id}-copy-${Date.now().toString(36)}`,
            name: `${source.name} (copy)`,
            modified: Date.now() / 1000,
          };
          return [copy, ...current];
        }),
      [],
    ),
    setSteps: useCallback(
      (id: string, steps: Step[]) => update(id, (program) => ({ ...program, steps })),
      [update],
    ),
  };
}
