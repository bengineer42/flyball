from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flyball.core import Channel, Operator, Positive, Reading
from flyball.core.clock import Duration
from flyball.core.sink import Observer
from flyball.runtime.rig import Rig

from .command import Activity, Command


class Prompt(Activity):
    """Fires when someone fires it: an operator prompt, or an external trigger."""

    __slots__ = ()


@dataclass(frozen=True)
class Wait(Command, tag="wait", primary="message"):
    """Pause the program until the named signal is fired.

    `name` is what it is fired by (`POST /api/signals/{name}/fire`), default
    `wait`. `timeout` gives up and ends the program.
    """

    message: str
    name: str | None = None
    timeout: Duration | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity:
        timeout = None if self.timeout is None else float(self.timeout)
        return Prompt(timeout, name=self.name, message=self.message)


class Sustained(Activity, Observer[Reading]):
    """Fires when a test on one channel's readings passes."""

    __slots__ = ("channel", "test")

    name: str  # pyright: ignore[reportIncompatibleVariableOverride]  an observer is always named

    def __init__(
        self,
        channel: Channel,
        test: Callable[[Reading], bool],
        timeout: Positive | None = None,
        name: str | None = None,
        message: str | None = None,
    ) -> None:
        super().__init__(
            timeout,
            name=name or f"sustained:{channel.name}",
            message=message or f"{channel.name} to pass {test.__name__}",
        )
        self.channel = channel
        self.test = test
        self.observes = frozenset((channel,))

    def observe(self, sample: Reading) -> None:
        if self.test(sample):
            self.fire()

    def attach(self, rig: Rig) -> None:
        rig.attach_observer(self)

    def detach(self, rig: Rig) -> None:
        rig.detach_observer(self)
