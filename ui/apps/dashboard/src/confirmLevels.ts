/**
 * How much a person confirms before an action, by action. One table so the levels are easy to
 * change: brain's confirm-levels draft is not decided yet, and these are its recommended levels.
 *
 * 0: no dialog. 1: a popup (title, consequence, Cancel / action). 2: the popup plus typing the
 * thing's name to enable the action. 3 (re-authenticate) waits for the server to enforce it.
 */
export type ConfirmLevel = 0 | 1 | 2;

export const CONFIRM_LEVELS = {
  /** Software stop: never above 1 -- friction on a stop is itself a hazard. */
  "rig.stop": 1,
  /** Letting a latch go: re-enables what the stop or fault held. */
  "rig.reset": 1,
  /** Adding a link or device: a rig edit, so the rig restarts. */
  "rig.edit.add": 1,
  /** Removing a link, device or controller from the rig file: a rig edit, so the rig restarts. */
  "rig.edit.remove": 2,
  /** Restoring a version: a new version on top, and the rig restarts on it. */
  "rig.restore": 2,
} as const satisfies Record<string, ConfirmLevel>;

export type ConfirmAction = keyof typeof CONFIRM_LEVELS;

export const confirmLevel = (action: ConfirmAction): ConfirmLevel => CONFIRM_LEVELS[action];
