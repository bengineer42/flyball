"""Sequencing: runs commands and programs against a rig.

The rig is what the equipment *is* -- pumps, readers, controller, loop, topics.
The programmer is what it is *doing*. Keeping them apart stops "abort the
program" from tangling with "stop the pumps", and gives each its own lock.

A single command is a program of one step, so there is one execution path, one
interrupt, and one answer to "what is running".

Ownership of an :class:`~humctrl.runners.Activity` is split: the rig *drives*
it, because it owns the clock and the sensors; the programmer owns its
*lifetime*, because it owns the sequence. So attach and detach bracket the wait
in :meth:`Programmer._wait_out`, and teardown is a single ``finally`` reached by
completion, failure and cancellation alike.

Locking:
    The programmer's lock is always the inner lock. :meth:`_apply` takes the
    rig's lock and then this one; nothing here takes the rig's lock while
    already holding this one, and no thread is ever joined under it.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock, Thread, current_thread
from typing import TYPE_CHECKING, Any

from humctrl.resource import Operator

from .errors import CommandRuntimeError, ProgramAlreadyRunningError
from .program import Program

if TYPE_CHECKING:
    from humctrl.programmer.command import Activity, Command
    from humctrl.rig import Rig


@dataclass(frozen=True, slots=True)
class ProgrammerState:
    """What the programmer is doing, for telemetry and the API."""

    running: bool
    step: int
    """Index of the step being run, zero when idle."""
    steps: int
    """Steps in the running program, zero when idle."""
    command: str | None
    """Tag of the step being run, ``None`` when idle."""


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
                return IDLE
            return ProgrammerState(
                running=True,
                step=self._step,
                steps=len(program),
                command=program[self._step].tag,
            )

    # endregion

    # region Running

    def start(self, work: Command[Any] | Program, interrupt: bool = False) -> None:
        """Begin ``work`` and return without waiting for it to finish.

        The first step is applied on the calling thread, so a command that
        cannot be applied raises here rather than disappearing into the
        warnings topic. Only what is left over -- an activity to wait out, or
        later steps -- goes to the worker thread.

        Args:
            work: A command, or a program of them.
            interrupt: Stop whatever is running first.

        Raises:
            ProgramAlreadyRunningError: Something is still running and
                ``interrupt`` was not asked for.
        """
        if interrupt:
            self.interrupt()
        program = self.load(work)

        with self.lock:
            activity = self._apply_atomics(program)
            self.rig.publish_state()

        thread = Thread(target=self._work, args=(program, activity), daemon=True, name="programmer")
        with self.lock:
            self._thread = thread
        thread.start()

    def load(self, work: Program | Command) -> Program:
        with self.lock:
            if self._program is not None:
                raise ProgramAlreadyRunningError(self._program, work)
            self._program = work = work if isinstance(work, Program) else Program([work])
            self._step = 0
            self._abort = False
            self._activity = None
            return self._program

    def run(self, work: Command[Any] | Program, interrupt: bool = False) -> None:
        """Apply ``work`` and block until it has finished or been interrupted.

        For tests, the CLI and anything else off the request path. A program is
        minutes long, so routes want :meth:`start`.
        """
        self.start(work, interrupt)
        self.join()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the running program, if any. A no-op called from the worker."""
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
        try:
            with self.lock:
                while self.step < len(program) and not self._abort:
                    if (activity := self._apply(program[self.step])) is not None:
                        return activity
                    self.step += 1
        except Exception as error:
            self.rig.publish_warning(CommandRuntimeError(program[self.step], self.step, error))

    # endregion
    # region Internals
    def _work(self, program: Program, activity: Activity | None) -> None:
        """Wait out the current step, then apply the rest in order."""
        step = 0
        try:
            while True:
                if activity is not None and not self._wait_out(activity):
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
            self.rig.publish_warning(CommandRuntimeError(program[step], step, error))
        finally:
            self._finish(program)

    def _wait_out(self, activity: Activity) -> bool:
        """Run ``activity`` to its end.

        Returns:
            False if the program should stop -- the activity was cancelled.

        Raises:
            Exception: Whatever the activity failed with, re-raised on this
                thread so :meth:`_work` ends the program rather than treating
                the step as done.
        """
        activity.attach(self.rig)
        try:
            activity.wait()
        finally:
            activity.detach(self.rig)

        if activity.error is not None:
            raise activity.error
        return not activity.interrupted

    def _apply(self, command: Command) -> Activity | None:
        """Apply one step under the rig's lock.

        Returns:
            The activity to wait out before the next step, or ``None`` to move
            straight on. A step with no activity is a setting -- open a valve,
            raise a flag.
        """
        with self.rig.lock:
            activity = command.run(self.rig, self.operator)
        with self.lock:
            self._activity = activity
        return activity

    def _finish(self, program: Program) -> None:
        """Clear ``program``, unless something else has already replaced it."""
        with self.lock:
            if self._program is program:
                self._program = None
                self._activity = None
                self._thread = None
                self._step = 0
                self._abort = False

    # endregion


# =============================================================================
# NOT DONE YET -- what this file needs from elsewhere.
#
# 1. Rig.attach / Rig.detach do not exist (rig.py). Membership only, under
#    rig.lock; the loop never detaches, because the programmer owns lifetime:
#
#        def attach(self, activity: Activity) -> None:
#            with self.lock:
#                if self._activity is not None:
#                    raise ActivityAlreadyRunningError(self._activity, activity)
#                self._activity = activity
#
#        def detach(self, activity: Activity) -> None:
#            with self.lock:
#                if self._activity is activity:
#                    self._activity = None
#                    activity.detach(self)
#
# 2. rig.main_step (rig.py:661) still steps ``self._runner``. It should step
#    ``self._activity`` when the signal is not already set, with the call
#    wrapped so a raising activity fails its signal rather than throwing every
#    tick -- ``activity.fail(error)``.
#
# 3. command.parse_response tests ``isinstance(value, Signal)`` and puts the
#    result in the ``activity`` slot. Since Activity now holds a signal rather
#    than being one, that branch wants ``Activity``, or a bare signal wrapped
#    in one.
#
# 4. Ownership. The rig must not own the programmer, or the split is undone.
#    ``daemon.py`` builds both and ``server/deps.py`` injects both; the route
#    composes ``rig.state`` with ``programmer.state`` rather than ``Rig.state``
#    reaching for a back-reference.
#
# Open question, not decided: a program that ends leaves the last generator
# installed and the controller running. Correct for a soak, wrong for a run
# that should return the rig to idle. Probably a terminal step rather than
# implicit teardown here.
# =============================================================================
