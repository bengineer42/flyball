"""Sequencing: runs commands and programs against a rig.

The rig is what the equipment *is*; the programmer is what it is *doing*. Each
has its own lock, so "abort the program" never tangles with "stop the pumps".
A single step is a program of one step: one execution path, one way to end it.

The rig *drives* an [Activity][flyball.sequencing.Activity] (it owns the clock
and sensors); the programmer owns its *lifetime* (it owns the sequence). Attach
and detach bracket the wait in `Programmer._wait_out`, and teardown is one
`finally` reached by completion, failure and cancellation alike.

Locking:
    The programmer's lock is never held while the rig's is taken: `_apply`
    takes the programmer's lock, releases it, emits the step event with no lock
    held, takes the rig's lock to run the step (unless the step takes it itself:
    a device command, which may wait), then takes the programmer's lock again;
    `_apply_atomics` on `start` does the same, step by step. So the only order
    is rig, then programmer -- `on_revoke` -> `interrupt` from a thread holding
    the rig's lock -- and it cannot deadlock against the other. No thread is
    joined under the programmer's lock, and `cancel`/`interrupt` join the
    worker for at most `END_JOIN_S`: a caller holding the rig's lock (which the
    worker's next step needs) waits that long, not for ever, and a step still
    running then is reported (`step_still_running`).

    [Inference, traced 23 Sep] Nothing revokes the programmer's operator today:
    no step claims a resource and no `Arbiter` is built outside tests, so
    `on_revoke` is not called. The order above is what keeps it safe when one is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import RLock, Thread, current_thread
from typing import TYPE_CHECKING, Any

from flyball.foundation.device import Code, Severity, SubjectKind
from flyball.foundation.resource import Operator

from .activities import Prompted
from .devices import RunCommand
from .errors import ProgramAlreadyRunningError, StepRuntimeError
from .program import Program

if TYPE_CHECKING:
    from flyball.rig import Rig
    from flyball.rig.latches import Latch
    from flyball.sequencing.step import Activity, Step

log = logging.getLogger("flyball.programmer")

END_JOIN_S = 5.0
"""How long `cancel`/`interrupt` wait for the worker to unwind before reporting it still running
and returning. A stop must return promptly; a step in a driver cannot be cut from outside."""


@dataclass(frozen=True, slots=True)
class ProgrammerState:
    """What the programmer is doing, for telemetry and the API."""

    running: bool
    step: int
    """Index of the step being run, zero when idle."""
    steps: int
    """Steps in the running program, zero when idle."""
    type: str | None
    """Type of the step being run, `None` when idle."""
    failed: bool = False
    """The last program ended with a step that raised, rather than succeeding or being ended."""
    error: str | None = None
    """What the failing step raised, while `failed`; cleared by the next `start`/`run`."""


IDLE = ProgrammerState(running=False, step=0, steps=0, type=None)


class Programmer:
    """Applies commands to a rig, in order, waiting where a step says to wait."""

    rig: Rig
    lock: RLock

    _program: Program | None = None
    _activity: Activity | None = None
    _thread: Thread | None = None
    _step: int = 0
    _abort: bool = False
    _interrupted: str | None = None
    """Why the engine ended the program (a stop, a shutdown); None when a person cancelled it."""
    _error: Exception | None = None
    """Set by `_failed`; read by `state` and `_finish` until the next `load` clears it."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.lock = RLock()
        self.operator = Operator(
            "program", on_revoke=lambda: self.interrupt("its claim on the rig was revoked")
        )
        rig.stopping.latches.on_change.append(self._latched)

    def _latched(self, latch: Latch, held: bool) -> None:
        """A controller's `on_fault` latched something: the running program ends, `fault:<name>`.

        On a thread of its own: the latch is set under the rig's lock, and ending a
        program waits for its worker, which takes that lock on its way out. A rig stop
        interrupts the program itself.
        """
        if not held or not latch.cause.startswith("on_fault:") or not self.running:
            return
        reason = f"fault:{latch.cause.removeprefix('on_fault:')}"
        Thread(target=self.interrupt, args=(reason,), daemon=True, name="fault-interrupt").start()

    # region Status

    @property
    def running(self) -> bool:
        """A program is part way through: waiting, or with steps still to come."""
        with self.lock:
            return self._program is not None

    @property
    def state(self) -> ProgrammerState:
        with self.lock:
            program = self._program
            if program is None:
                if self._error is None:
                    return IDLE
                return ProgrammerState(
                    running=False,
                    step=0,
                    steps=0,
                    type=None,
                    failed=True,
                    error=str(self._error),
                )
            return ProgrammerState(
                running=True,
                step=self._step,
                steps=len(program),
                type=program[self._step].type,
            )

    # endregion

    # region Running

    def start(self, work: Step | Program, cancel: bool = False) -> None:
        """Begin `work` without waiting for it to finish.

        The first step is applied on the calling thread, so an unapplicable
        step raises here; the rest goes to the worker thread.

        Args:
            work: A step, or a program of them.
            cancel: Cancel whatever is running first.

        Raises:
            ProgramAlreadyRunningError: Something is running and `cancel`
                is false.
        """
        if cancel:
            self.cancel()
        program = self.load(work)
        self.rig.event(
            Severity.INFO,
            SubjectKind.PROGRAM,
            program.name or "program",
            Code.STARTED,
            f"{program.name or 'program'}: {len(program)} step{'s' if len(program) != 1 else ''}",
            {"steps": len(program), "types": [c.type for c in program]},
        )
        try:
            activity = self._apply_atomics(program)
        except Exception as error:
            with self.lock:
                step = self._step
            self._failed(program, step, error)
            self._finish(program)
            raise
        if activity is None:  # every step applied at once; nothing to wait for
            self._finish(program)
            return

        thread = Thread(target=self._work, args=(program, activity), daemon=True, name="programmer")
        with self.lock:
            self._thread = thread
        thread.start()

    def load(self, work: Program | Step) -> Program:
        with self.lock:
            if self._program is not None:
                raise ProgramAlreadyRunningError(self._program, work)
            self._program = work = work if isinstance(work, Program) else Program([work])
            self._step = 0
            self._abort = False
            self._interrupted = None
            self._activity = None
            self._error = None
            return self._program

    def run(self, work: Step | Program, cancel: bool = False) -> None:
        """Apply `work` and block until it ends.

        For use off the request path; routes want
        [start][flyball.sequencing.programmer.Programmer.start].
        """
        self.start(work, cancel)
        self.join()

    def join(self, timeout: float | None = None) -> None:
        """Signal for the running program, if any. A no-op called from the worker."""
        with self.lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout)

    def cancel(self) -> bool:
        """End whatever is running, as a person asked: it ends `cancelled`. Outputs are kept.

        Returns whether the worker unwound within `END_JOIN_S`.
        """
        return self._end(None)

    def interrupt(self, reason: str) -> bool:
        """End whatever is running because the engine must (a stop, a shutdown).

        It ends `interrupted`, with `reason`. Outputs are kept. Returns whether
        the worker unwound within `END_JOIN_S`.
        """
        return self._end(reason)

    def _end(self, reason: str | None) -> bool:
        """Stop whatever is running, and wait at most `END_JOIN_S` for the worker to unwind.

        A worker still in its step then (a command in its driver, which Python
        cannot cut) is left to finish on its own: it applies nothing more, and a
        `step_still_running` event names the step. Returns whether it unwound.
        """
        with self.lock:
            if self._program is not None and not self._abort:
                self._interrupted = reason
            self._abort = True
            if self._activity is not None:
                # Cancels rather than completes, so the worker breaks out
                # instead of moving on. Its finally clause detaches.
                self._activity.interrupt()
            thread = self._thread
            program, step = self._program, self._step
        # A device command the step is running and waiting in (a dose, a move) ends too:
        # the program that asked for it has ended, so its wait is cancelled
        # (`Device.cancel`), and its own `finally` leaves the hardware as it would at its end.
        current = None if program is None or step >= len(program) else program[step]
        device = self.rig.devices.get(current.device) if isinstance(current, RunCommand) else None
        if device is not None and device in self.rig.running_commands():
            device.cancel()
        # Never join under the lock: the worker takes it on the way out, and
        # an RLock held by another thread does not help us here.
        if thread is None or thread is current_thread():
            return True
        thread.join(END_JOIN_S)
        if not thread.is_alive():
            return True
        name = program.name if program is not None and program.name else "program"
        step_type = program[step].type if program is not None and step < len(program) else "?"
        log.warning(
            "%s[%s]: %s had not returned %.1f s after the end", name, step, step_type, END_JOIN_S
        )
        self.rig.event(
            Severity.WARNING,
            SubjectKind.PROGRAM,
            f"{name}[{step}]",
            Code.STEP_STILL_RUNNING,
            f"{step_type} had not returned {END_JOIN_S:g} s after the program ended: "
            "it may still act",
            {"index": step, "step": step_type, "waited_s": END_JOIN_S},
        )
        return False

    def _apply_atomics(self, program: Program) -> Activity | None:
        """Apply steps from the current one until one has to be waited on.

        Returns:
            That step's activity, or `None` if the rest of the program applied.
        """
        while True:
            # The programmer's lock is released around each step: it is never held while
            # the step takes the rig's (the rig-then-programmer order stays the only one),
            # nor while a device command waits, which `cancel` would otherwise queue behind.
            with self.lock:
                if self._step >= len(program) or self._abort:
                    return None
                step = program[self._step]
            if (activity := self._apply(step)) is not None:
                return activity
            with self.lock:
                self._step += 1

    # endregion
    # region Internals
    def _work(self, program: Program, activity: Activity | None) -> None:
        """Wait out the current step, then apply the rest in order."""
        with self.lock:
            step = self._step
        try:
            while True:
                if activity is not None:
                    with self.lock:
                        if self._abort:
                            break
                    if not self._wait_out(activity, program[step]):
                        self._ended_early(program, step, activity)
                        break
                step += 1
                with self.lock:
                    if self._abort or self._program is not program or step >= len(program):
                        break
                    self._step = step
                activity = self._apply(program[step])
        except Exception as error:
            # A step that will not apply, or an activity that failed, ends the
            # program. There is nobody left to raise at, so the rig's watchers
            # hear about it.
            self._failed(program, step, error)
        finally:
            self._finish(program)

    def _wait_out(self, activity: Activity, command: Step) -> bool:
        """Run `activity` to its end, registered by name so it can be answered.

        Returns:
            False if the activity was cancelled or timed out, so the program
            stops here. A timeout is recorded as an event; a cancel or an
            interrupt is recorded when the program ends.

        Raises:
            Exception: Whatever the activity failed with, so `_work` ends the
                program rather than treating the step as done.
        """
        name = activity.name or command.type
        self.rig.triggers.register(
            name,
            activity,
            activity.message,
            activity.timeout_s,
            prompt=isinstance(activity, Prompted),
        )
        activity.attach(self.rig)
        try:
            activity.wait()
        finally:
            activity.detach(self.rig)
            self.rig.triggers.remove(name)

        if activity.error is not None:
            raise activity.error
        if activity.timed_out:
            program = self._program
            self.rig.event(
                Severity.WARNING,
                SubjectKind.PROGRAM,
                f"{program.name if program is not None and program.name else 'program'}"
                f"[{self._step}]",
                Code.STEP_TIMED_OUT,
                f"{command.type} gave up after {activity.timeout_s} s: {activity.message}",
                {"step": command.type, "timeout_s": activity.timeout_s},
            )
        return activity.fired

    def _ended_early(self, program: Program, step: int, activity: Activity) -> None:
        """The step's activity ended without being met: say how the program ends.

        Cancelled from outside (a person answering its prompt with cancel) is
        the program `cancelled`; timed out is the program `failed` at that
        step. A `cancel()` or `interrupt()` has already said which it is.
        """
        with self.lock:
            if self._abort or self._program is not program:
                return
            if activity.timed_out:
                self._error = StepRuntimeError(
                    program[step], step, TimeoutError(f"gave up after {activity.timeout_s} s")
                )
            else:
                self._abort = True
                self._interrupted = None

    def _apply(self, command: Step) -> Activity | None:
        """Apply one step: under the rig's lock, unless the step takes it itself (`locked`).

        Returns:
            The activity to wait out before the next step, or `None` to move
            straight on.
        """
        with self.lock:
            program, step = self._program, self._step
        self.rig.event(
            Severity.INFO,
            SubjectKind.PROGRAM,
            f"{program.name if program is not None and program.name else 'program'}[{step}]",
            Code.STEP,
            f"step {step + 1}/{len(program) if program is not None else '?'}: {command.type}",
            {"index": step, "step": command.type},
        )
        if command.locked:
            with self.rig.lock:
                activity = command.run(self.rig, self.operator)
        else:
            activity = command.run(self.rig, self.operator)
        with self.lock:
            self._activity = activity
            if self._abort and activity is not None:
                # A cancel or an interrupt landed while the step applied, so it
                # ended the previous activity rather than this one.
                activity.interrupt()
        return activity

    def _failed(self, program: Program, step: int, error: Exception) -> None:
        """A step that will not apply, or an activity that failed, ends the program: say so.

        Records `error` on the programmer itself, so `_finish` ends the program as
        `failed` rather than `succeeded`, and `state` keeps reporting it until the
        next `load` clears it.
        """
        failure = StepRuntimeError(program[step], step, error)
        with self.lock:
            self._error = failure
        self.rig.event(
            Severity.ERROR,
            SubjectKind.PROGRAM,
            f"{program.name or 'program'}[{step}]",
            Code.STEP_FAILED,
            str(failure),
            {"step": program[step].type, "error": f"{type(error).__name__}: {error}"},
        )

    def _finish(self, program: Program) -> None:
        """Clear `program`, unless something else has already replaced it, and say how it ended."""
        with self.lock:
            if self._program is not program:
                return
            error = self._error
            reason = self._interrupted
            outcome = (
                Code.FAILED
                if error is not None
                else (Code.INTERRUPTED if reason is not None else Code.CANCELLED)
                if self._abort
                else Code.SUCCEEDED
            )
            self._program = None
            self._activity = None
            self._thread = None
            self._step = 0
            self._abort = False
            self._interrupted = None
        name = program.name or "program"
        message = (
            f"{name} failed: {error}"
            if error is not None
            else f"{name} interrupted: {reason}"
            if outcome is Code.INTERRUPTED
            else f"{name} {outcome}"
        )
        details: dict[str, Any] = {"steps": len(program)}
        if error is not None:
            details["error"] = str(error)
        if outcome is Code.INTERRUPTED:
            details["reason"] = reason
        self.rig.event(
            Severity.ERROR if error is not None else Severity.INFO,
            SubjectKind.PROGRAM,
            program.name or "program",
            outcome,
            message,
            details,
        )

    # endregion
