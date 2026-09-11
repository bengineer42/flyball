from collections.abc import Callable
from threading import RLock
from typing import Protocol

from humctrl.control import ControlLawLike, Loop, Tuning
from humctrl.control.loop import Actuator
from humctrl.core import Clock
from humctrl.core.reading import Channel, Reading


class LoopNotFoundError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"Loop not found: {name}")


class NoDefaultLoopSetError(Exception):
    def __init__(self) -> None:
        super().__init__("No default loop set")


type RigOnTickCallback = Callable[[Rig, list[Reading]], None]


type OrderedSet[T] = dict[T, None]


class Router:
    loops: OrderedSet[Loop]
    actuators: OrderedSet[Actuator]


class Rig(Protocol):
    clock: Clock
    _on_tick: dict[RigOnTickCallback, None]
    lock: RLock

    def attach_loop(
        self, name: str, actuator: Actuator, law: ControlLawLike | str | None = None
    ) -> None: ...
    def resolve_loop(self, name: str | None = None) -> Loop: ...
    def attach_on_tick(self, callback: RigOnTickCallback) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick[callback] = None

    def detach_on_tick(self, callback: RigOnTickCallback) -> None:
        with self.lock:
            if callback is not None:
                self._on_tick.pop(callback, None)


class MultiLoopRig:
    tunings: dict[str, Tuning]
    default_loop: str | None
    loops: dict[str, Loop]
    channels: dict[str, Channel]

    def resolve_loop(self, name: str | None = None) -> Loop:
        name = name or self.default_loop
        if name is None:
            raise NoDefaultLoopSetError()
        try:
            return self.loops[name]
        except KeyError as e:
            raise LoopNotFoundError(name) from e


class SingleLoopRig[A: Actuator](Rig):
    loop: Loop[A]

    def resolve_loop(self, name: str | None = None) -> Loop[A]:
        if name and name != self.loop.name:
            raise LoopNotFoundError(name)
        return self.loop
