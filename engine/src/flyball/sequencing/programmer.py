"""Sequencing: runs commands and programs against a rig.

The rig is what the equipment *is*; the programmer is what it is *doing*. Each
has its own lock, so "abort the program" never tangles with "stop the pumps".
A single command is a program of one step: one execution path, one interrupt.

The rig *drives* an [Activity][flyball.sequencing.Activity] (it owns the clock
and sensors); the programmer owns its *lifetime* (it owns the sequence). Attach
and detach bracket the wait in `Programmer._wait_out`, and teardown is one
`finally` reached by completion, failure and cancellation alike.

Locking:
    On the worker path the two locks are never nested: `_apply` takes the
    programmer's lock, releases it, emits the step event with no lock held,
    takes the rig's lock to run the command, then takes the programmer's lock
    again. On `start`, `_apply_atomics` holds the programmer's lock around its
    `_apply` calls, so there the rig's lock is taken inside it (programmer, then
    rig). No thread is joined under either lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock, Thread, current_thread
from typing import TYPE_CHECKING, Any

from flyball.foundation.device import Kind, Level, Scope
from flyball.foundation.resource import Operator

from .activities import Prompted
from .errors import ProgramAlreadyRunningError, StepRuntimeError
from .program import Program

if TYPE_CHECKING:
    from flyball.rig import Rig
    from flyball.sequencing.step import Activity, Step


@dataclass(frozen=True, slots=True)
class ProgrammerState:
    """What the programmer is doing, for telemetry and the API."""

    running: bool
    step: int
    """Index of the step being run, zero when idle."""
    steps: int
    """Steps in the running program, zero when idle."""
    command: str | None
    """Tag of the step being run, `None` when idle."""
    failed: bool = False
    """The last program ended with a step that raised, rather than finishing or interrupting."""
    error: str | None = None
    """What the failing step raised, while `failed`; cleared by the next `start`/`run`."""


IDLE = ProgrammerState(running=False, step=0, steps=0, command=None)


class Programmer:
    """Applies commands to a rig, in order, waiting where a step says to wait."""

    rig: Rig
    lock: RLock

    _program: Program | None = None
    _activity: Activity | None = None
    _thread: Thread | None = None
    _step: int = 0
    _abort: bool = False
    _error: Exception | None = None
    """Set by `_failed`; read by `state` and `_finish` until the next `load` clears it."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.lock = RLock()
        self.operator = Operator("program", on_revoke=self.interrupt)

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
                    command=None,
                    failed=True,
                    error=str(self._error),
                )
            return ProgrammerState(
                running=True,
                step=self._step,
                steps=len(program),
                command=program[self._step].tag,
            )

    # endregion

    # region Running

    def start(self, work: Step | Program, interrupt: bool = False) -> None:
        """Begin `work` without waiting for it to finish.

        The first step is applied on the calling thread, so an unapplicable
        command raises here; the rest goes to the worker thread.

        Args:
            work: A command, or a program of them.
            interrupt: Stop whatever is running first.

        Raises:
            ProgramAlreadyRunningError: Something is running and `interrupt`
                is false.
        """
        if interrupt:
            self.interrupt()
        program = self.load(work)
        self.rig.event(
            Level.INFO,
            Scope.PROGRAM,
            program.name or "program",
            Kind.STARTED,
            f"{program.name or 'program'}: {len(program)} step{'s' if len(program) != 1 else ''}",
            {"steps": len(program), "commands": [c.tag for c in program]},
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
            self._activity = None
            self._error = None
            return self._program

    def run(self, work: Step | Program, interrupt: bool = False) -> None:
        """Apply `work` and block until it finishes or is interrupted.

        For use off the request path; routes want
        [start][flyball.sequencing.programmer.Programmer.start].
        """
        self.start(work, interrupt)
        self.join()

    def join(self, timeout: float | None = None) -> None:
        """Signal for the running program, if any. A no-op called from the worker."""
        with self.lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout)

    def interrupt(self) -> None:
        """Stop whatever is running, and wait for the worker to unwind."""
        with self.lock:
            self._abort = True
            if self._activity is not None:
                # Cancels rather than completes, so the worker breaks out
                # instead of moving on. Its finally clause detaches.
                self._activity.interrupt()
            thread = self._thread
        # Never join under the lock: the worker takes it on the way out, and
        # an RLock held by another thread does not help us here.
        if thread is not None and thread is not current_thread():
            thread.join()

    def _apply_atomics(self, program: Program) -> Activity | None:
        """Apply steps from the current one until one has to be waited on.

        Returns:
            That step's activity, or `None` if the rest of the program applied.
        """
        with self.lock:
            while self._step < len(program) and not self._abort:
                if (activity := self._apply(program[self._step])) is not None:
                    return activity
                self._step += 1
        return None

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
            stops here. A timeout is recorded as an event; an interrupt was
            asked for and is not.

        Raises:
            Exception: Whatever the activity failed with, so `_work` ends the
                program rather than treating the step as done.
        """
        name = activity.name or command.tag
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
                Level.WARNING,
                Scope.PROGRAM,
                f"{program.name if program is not None and program.name else 'program'}"
                f"[{self._step}]",
                Kind.STEP_TIMED_OUT,
                f"{command.tag} gave up after {activity.timeout_s} s: {activity.message}",
                {"command": command.tag, "timeout_s": activity.timeout_s},
            )
        return activity.fired

    def _apply(self, command: Step) -> Activity | None:
        """Apply one step under the rig's lock.

        Returns:
            The activity to wait out before the next step, or `None` to move
            straight on.
        """
        with self.lock:
            program, step = self._program, self._step
        self.rig.event(
            Level.INFO,
            Scope.PROGRAM,
            f"{program.name if program is not None and program.name else 'program'}[{step}]",
            Kind.STEP,
            f"step {step + 1}/{len(program) if program is not None else '?'}: {command.tag}",
            {"step": step, "command": command.tag},
        )
        with self.rig.lock:
            activity = command.run(self.rig, self.operator)
        with self.lock:
            self._activity = activity
            if self._abort and activity is not None:
                # `interrupt` landed while the step applied, so it cancelled the
                # previous activity rather than this one.
                activity.interrupt()
        return activity

    def _failed(self, program: Program, step: int, error: Exception) -> None:
        """A step that will not apply, or an activity that failed, ends the program: say so.

        Records `error` on the programmer itself, so `_finish` ends the program as
        `failed` rather than `finished`, and `state` keeps reporting it until the
        next `load` clears it.
        """
        failure = StepRuntimeError(program[step], step, error)
        with self.lock:
            self._error = failure
        self.rig.event(
            Level.ERROR,
            Scope.PROGRAM,
            f"{program.name or 'program'}[{step}]",
            Kind.STEP_FAILED,
            str(failure),
            {"command": program[step].tag, "error": f"{type(error).__name__}: {error}"},
        )

    def _finish(self, program: Program) -> None:
        """Clear `program`, unless something else has already replaced it, and say how it ended."""
        with self.lock:
            if self._program is not program:
                return
            error = self._error
            outcome = (
                Kind.FAILED
                if error is not None
                else Kind.INTERRUPTED
                if self._abort
                else Kind.FINISHED
            )
            self._program = None
            self._activity = None
            self._thread = None
            self._step = 0
            self._abort = False
        message = (
            f"{program.name or 'program'} failed: {error}"
            if error is not None
            else (f"{program.name or 'program'} {outcome}")
        )
        details: dict[str, Any] = {"steps": len(program)}
        if error is not None:
            details["error"] = str(error)
        self.rig.event(
            Level.ERROR if error is not None else Level.INFO,
            Scope.PROGRAM,
            program.name or "program",
            outcome,
            message,
            details,
        )

    # endregion
