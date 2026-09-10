from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar, NamedTuple, overload

from pydantic.alias_generators import to_snake

from humctrl.control import Loop
from humctrl.core import Operator, Reading, Signal

Commands: dict[str, type[Command]] = {}


class Activity:
    """The ongoing part of a command: driven by the rig, waited on by the programmer."""

    signal: Signal
    error: Exception | None = None

    def __init__(self, signal: Signal | None = None) -> None:
        self.signal = signal or Signal()

    def tick(self, rig: Any, reading: Reading) -> None:
        """Called once per loop tick while attached. Default: nothing."""

    def detach(self, rig: Any) -> None:
        """Put back whatever this took over. Default: nothing."""

    def finish(self) -> None:
        self.signal.fire()

    def fail(self, error: Exception) -> None:
        """Give up. The waiter re-raises this instead of moving on.

        Fires rather than cancels: the activity ended, it was not interrupted.
        """
        self.error = error
        self.signal.fire()

    @property
    def on_tick(self) -> Callable[[Any, Any], None] | None:
        return self.tick

    @property
    def interrupted(self) -> bool:
        return self.signal.interrupted

    def set(self) -> None:
        self.signal.set()

    def fire(self) -> None:
        self.signal.fire()

    __hash__ = object.__hash__


class LoopActivity:
    """The ongoing part of a command: driven by the rig, waited on by the programmer."""

    signal: Signal
    error: Exception | None = None

    def __init__(self, signal: Signal | None = None) -> None:
        self.signal = signal or Signal()

    def tick(self, loop: Loop, reading: Reading | None) -> None:
        """Called once per loop tick while attached. Default: nothing."""

    def detach(self, loop: Loop) -> None:
        """Put back whatever this took over. Default: nothing."""

    def finish(self) -> None:
        self.signal.fire()

    def fail(self, error: Exception) -> None:
        """Give up. The waiter re-raises this instead of moving on.

        Fires rather than cancels: the activity ended, it was not interrupted.
        """
        self.error = error
        self.signal.fire()

    @property
    def on_tick(self) -> Callable[[Loop, Reading | None], None] | None:
        return self.tick

    @property
    def interrupted(self) -> bool:
        return self.signal.interrupted

    def set(self) -> None:
        self.signal.set()

    def fire(self) -> None:
        self.signal.fire()

    __hash__ = object.__hash__


class SignalActivity(Activity):
    @property
    def on_tick(self) -> Callable[[Any, Reading], None] | None:
        return None


class CommandResult[T](NamedTuple):
    value: T
    activity: Activity | None = None

    @overload
    @classmethod
    def parse(
        cls, activity: Activity | Signal | None = None, value: T | None = None
    ) -> CommandResult[T]: ...
    @overload
    @classmethod
    def parse(
        cls, value: T | None = None, activity: Activity | Signal | None = None
    ) -> CommandResult[T]: ...
    @overload
    @classmethod
    def parse(cls, result: CommandResult[T] | Activity | Signal | T) -> CommandResult[T]: ...
    @classmethod
    def parse(cls, *args, **kwargs) -> CommandResult[T]:
        value, activity = kwargs.get("value"), kwargs.get("activity")
        for arg in args:
            if isinstance(arg, CommandResult):
                return arg
            elif isinstance(arg, Activity):
                activity = arg
            elif isinstance(arg, Signal):
                activity = SignalActivity(arg)
            else:
                value = arg
        return cls(value=value, activity=activity)  # pyright: ignore[reportArgumentType]


class Command[T]:
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
    def run(
        self, rig: Any, operator: Operator | None = None
    ) -> CommandResult[T] | Activity | Signal | T:
        """Do the work, returning a runner if it has to be waited on."""
