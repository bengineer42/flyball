from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from pydantic.alias_generators import to_snake

from flyball.foundation import Operator, Trigger
from flyball.foundation.time import Clock
from flyball.rig import Rig


class Activity(Trigger):
    """A signal a program step waits on, that hooks itself into the rig.

    `attach` registers whatever feeds it; `detach` undoes that. `fail` fires
    with an error the waiter re-raises.
    """

    __slots__ = ("error", "message", "name", "timeout_s")

    error: Exception | None
    name: str | None
    """The name the wait is registered under (the command's tag when None) and its message."""
    message: str | None
    timeout_s: float | None

    def __init__(
        self,
        timeout: float | None = None,
        name: str | None = None,
        message: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__(timeout, clock)
        self.error = None
        self.name = name
        self.message = message
        self.timeout_s = timeout

    def attach(self, rig: Rig) -> None:
        """Hook in. Default: nothing -- a pure wait."""

    def detach(self, rig: Rig) -> None:
        """Undo attach. Default: nothing."""

    def fail(self, error: Exception) -> bool:
        self.error = error
        return self.fire()


class Command:
    """Base for everything a program can run.

    `primary` names the field a bare scalar means in a program file, so
    `- flag: "loaded"` stands for `- flag: {flag: "loaded"}`; None means no
    shorthand. Subclassing sets `tag`/`primary`; registering it so a program
    file can use it is a separate, explicit step -- see
    [Catalogs.register_command][flyball.model.catalog.Catalogs.register_command].
    """

    tag: ClassVar[str] = ""
    primary: ClassVar[str | None] = None

    def __init_subclass__(
        cls,
        tag: str | None = None,
        primary: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or to_snake(cls.__name__)
        if primary is not None:
            cls.primary = primary

    @abstractmethod
    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        """Do the work; return an activity if the program must wait on it, else None."""

    def missing(self, rig: Rig) -> list[str]:
        """What this command names that `rig` lacks right now: a controller, a tuning, a device.

        Advice for a check, not a verdict: the rig may gain them before the
        program runs, and `run` raises for itself. Default: nothing named.
        """
        return []
