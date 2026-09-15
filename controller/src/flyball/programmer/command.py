from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar

from pydantic.alias_generators import to_snake

from flyball.core import Operator, Signal
from flyball.runtime.rig import Rig

Commands: dict[str, type[Command]] = {}


class Activity(Signal):
    """A signal a program step waits on, that knows how to hook itself into the rig.

    ``attach`` registers whatever feeds it -- an observer, a timer, a prompt;
    ``detach`` undoes that and puts back anything taken over. ``fail`` fires
    with an error the waiter re-raises: the activity ended, it was not
    interrupted.
    """

    __slots__ = ("error",)

    error: Exception | None

    def __init__(self, timeout: float | None = None) -> None:
        super().__init__(timeout)
        self.error = None

    def attach(self, rig: Rig) -> None:
        """Hook in. Default: nothing -- a pure wait."""

    def detach(self, rig: Rig) -> None:
        """Undo attach. Default: nothing."""

    def fail(self, error: Exception) -> bool:
        self.error = error
        return self.fire()


# class CommandResult[T](NamedTuple):
#     value: T
#     activity: Activity | None = None

#     @overload
#     @classmethod
#     def parse(
#         cls, activity: Activity | Signal | None = None, value: T | None = None
#     ) -> CommandResult[T]: ...
#     @overload
#     @classmethod
#     def parse(
#         cls, value: T | None = None, activity: Activity | Signal | None = None
#     ) -> CommandResult[T]: ...
#     @overload
#     @classmethod
#     def parse(cls, result: CommandResult[T] | Activity | Signal | T) -> CommandResult[T]: ...
#     @classmethod
#     def parse(cls, *args, **kwargs) -> CommandResult[T]:
#         value, activity = kwargs.get("value"), kwargs.get("activity")
#         for arg in args:
#             if isinstance(arg, CommandResult):
#                 return arg
#             elif isinstance(arg, Activity):
#                 activity = arg
#             elif isinstance(arg, Signal):
#                 activity = Activity(arg)
#             else:
#                 value = arg
#         return cls(value=value, activity=activity)  # pyright: ignore[reportArgumentType]


class Command:
    """Base for everything a program can run.

    Subclassing registers the command under its tag. The wire model is built by
    the server layer, which is the only place that knows how a domain type
    crosses the wire.
    """

    tag: ClassVar[str] = ""

    def __init_subclass__(
        cls, tag: str | None = None, register: bool = True, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or to_snake(cls.__name__)
        if not register:
            return
        clash = Commands.get(cls.tag)
        # ``@dataclass(slots=True)`` rebuilds the class, so this runs a second
        # time with a different object for the same command. Same qualified
        # name means the rebuild, not a clash.
        if clash is not None and (clash.__module__, clash.__qualname__) != (
            cls.__module__,
            cls.__qualname__,
        ):
            raise ValueError(f"tag {cls.tag!r} is already {clash.__name__}")
        Commands[cls.tag] = cls

    @abstractmethod
    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | Signal:
        """Do the work, returning a runner if it has to be waited on."""
