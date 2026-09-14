from humctrl.control import (
    Loop,
    LoopSpec,
    LoopState,
    LoopView,
)
from humctrl.core import Channel
from humctrl.core.errors import NotFoundError, NotReadyError


class LoopNotFoundError(NotFoundError):
    def __init__(self, name: Channel) -> None:
        super().__init__(f"Loop not found for channel: {name}")


class NoDefaultLoopError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("No default loop set")


class Loops:
    _loops: dict[Channel, Loop]
    default: Channel | None = None

    def __init__(self) -> None:
        self._loops = {}

    def __getitem__(self, name: Channel) -> Loop:
        return self._loops[name]

    def __iter__(self):
        return iter(self._loops)

    def add(self, name: Channel, loop: Loop, default: bool = False) -> None:
        self._loops[name] = loop
        if default:
            self.default = name

    def resolve(self, channel: Channel | None = None) -> Loop:
        channel = channel or self.default
        if channel is None:
            raise NoDefaultLoopError()
        try:
            return self._loops[channel]
        except KeyError as e:
            raise LoopNotFoundError(channel) from e

    def items(self):
        return self._loops.items()

    @property
    def states(self) -> dict[Channel, LoopState]:
        return {name: loop.state for name, loop in self._loops.items()}

    @property
    def specs(self) -> dict[Channel, LoopSpec]:
        return {name: loop.spec for name, loop in self._loops.items()}

    @property
    def views(self) -> dict[Channel, LoopView]:
        return {name: loop.view for name, loop in self._loops.items()}

    def spec(self, name: Channel) -> LoopSpec:
        return self._loops[name].spec

    def view(self, name: Channel) -> LoopView:
        return self._loops[name].view

    def state(self, name: Channel) -> LoopState:
        return self._loops[name].state
