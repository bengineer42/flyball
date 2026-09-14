from __future__ import annotations

from collections.abc import Callable

from humctrl.core import Channel, Positive, Reading
from humctrl.core.sink import Observer
from humctrl.runtime.rig import Rig

from .command import Activity


class Sustained(Activity, Observer):
    """Fires when a test on one channel's readings passes."""

    __slots__ = ("channel", "test")

    def __init__(
        self,
        channel: Channel,
        test: Callable[[Reading], bool],
        timeout: Positive | None = None,
    ) -> None:
        super().__init__(timeout)
        self.channel = channel
        self.test = test
        self.observes = frozenset((channel,))

    def observe(self, reading: Reading) -> None:
        if self.test(reading):
            self.fire()

    def attach(self, rig: Rig) -> None:
        rig.attach_observer(self)

    def detach(self, rig: Rig) -> None:
        rig.detach_observer(self)
