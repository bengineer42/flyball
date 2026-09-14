from collections.abc import ItemsView, Iterator
from typing import Any

from humctrl.control import Loop, LoopSpec, LoopState, LoopView
from humctrl.core import Channel
from humctrl.core.errors import ConflictError, NotFoundError, NotReadyError


class LoopNotFoundError(NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Loop {name!r} not found")


class NoDefaultLoopError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("No default loop set")


class ChannelClaimedError(ConflictError):
    def __init__(self, channel: Channel, by: str) -> None:
        super().__init__(f"{channel.name} is already regulated by loop {by!r}")


class Loops:
    """The rig's loops: by name for people and programs, by channel for the tick.

    A loop's name is its actuator's. One loop per channel, one channel per
    loop -- two loops on one measurement would fight.
    """

    _loops: dict[str, Loop[Any]]
    _channels: dict[str, Channel]
    _process: dict[Channel, Loop[Any]]
    default: str | None = None

    def __init__(self) -> None:
        self._loops = {}
        self._channels = {}
        self._process = {}

    def __getitem__(self, name: str) -> Loop[Any]:
        return self._loops[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._loops)

    def __contains__(self, name: str) -> bool:
        return name in self._loops

    def __len__(self) -> int:
        return len(self._loops)

    def add(self, channel: Channel, loop: Loop[Any], default: bool = False) -> None:
        if (holder := self._process.get(channel)) is not None and holder is not loop:
            raise ChannelClaimedError(channel, holder.name)
        self._loops[loop.name] = loop
        self._channels[loop.name] = channel
        self._process[channel] = loop
        if default or self.default is None:
            self.default = loop.name

    def find(self, channel: Channel) -> Loop[Any] | None:
        """The loop regulating ``channel``, or None. The hot path: most channels have none."""
        return self._process.get(channel)

    def resolve(self, name: str | None = None) -> Loop[Any]:
        """By name, or the default. Raises rather than returning None."""
        name = name or self.default
        if name is None:
            raise NoDefaultLoopError()
        try:
            return self._loops[name]
        except KeyError as e:
            raise LoopNotFoundError(name) from e

    def channel(self, name: str) -> Channel:
        try:
            return self._channels[name]
        except KeyError as e:
            raise LoopNotFoundError(name) from e

    def items(self) -> ItemsView[str, Loop[Any]]:
        return self._loops.items()

    def entries(self) -> Iterator[tuple[Channel, Loop[Any]]]:
        """Every loop with the channel it regulates."""
        return ((self._channels[name], loop) for name, loop in self._loops.items())

    @property
    def states(self) -> dict[str, LoopState]:
        return {name: loop.state for name, loop in self._loops.items()}

    @property
    def specs(self) -> dict[str, LoopSpec]:
        return {name: loop.spec for name, loop in self._loops.items()}

    @property
    def views(self) -> dict[str, LoopView]:
        return {name: loop.view for name, loop in self._loops.items()}
