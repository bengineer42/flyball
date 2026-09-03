/**
 * The result of the last command.
 *
 * The daemon's refusals are prose ("No pumps attached to this rig", "Flows
 * exceed the pumps"), so they are shown verbatim rather than reduced to a
 * status code the operator would have to look up.
 */

import { useEffect } from "react";

import type { CommandState } from "../state/useRig";
import { Note } from "./primitives";

export interface CommandFeedbackProps {
  command: CommandState;
  onDismiss(): void;
}

export function CommandFeedback({ command, onDismiss }: CommandFeedbackProps) {
  // Success is transient; a failure stays until it is read and dismissed.
  useEffect(() => {
    if (!command.done) return;
    const timer = window.setTimeout(onDismiss, 2500);
    return () => window.clearTimeout(timer);
  }, [command.done, onDismiss]);

  if (command.error) {
    return (
      <Note tone="critical">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>
            <strong>Refused:</strong> {command.error}
          </span>
          <button type="button" className="ghost small" onClick={onDismiss}>
            Dismiss
          </button>
        </div>
      </Note>
    );
  }
  if (command.done) return <Note tone="neutral">{command.done} — done.</Note>;
  return null;
}
