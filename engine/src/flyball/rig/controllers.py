"""The rig's controllers: by target address for people and programs, by source for the tick.

A controller is named by the signal it drives, so its name is that signal's
address (`"heaters.heater1"`). One controller per target and one per source:
two on a target would fight over it, two on a source would both step on
the same reading.
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator

from flyball.foundation.device import Signal
from flyball.foundation.errors import ConflictError, NotFoundError, NotReadyError
from flyball.model.controller import Controller, ControllerSettings, ControllerState, ControllerView


class ControllerNotFoundError(NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Controller {name!r} not found")


class NoDefaultControllerError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("No default controller set")


class SourceClaimedError(ConflictError):
    """A signal already has a controller on it: as its source, or as its target.

    A controller's name is its target's address, so a claimed target reads
    "driven by" and a claimed source "regulated by".
    """

    def __init__(self, signal: Signal, by: str) -> None:
        verb = "driven" if signal.address == by else "regulated"
        super().__init__(f"{signal.address} is already {verb} by controller {by!r}")


class Controllers:
    """The rig's controllers, keyed by target address; `find` by source is the hot path."""

    _controllers: dict[str, Controller]
    _sources: dict[Signal, Controller]
    _targets: dict[Signal, Controller]
    default: str | None = None

    def __init__(self) -> None:
        self._controllers = {}
        self._sources = {}
        self._targets = {}

    def __getitem__(self, name: str) -> Controller:
        return self._controllers[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._controllers)

    def __contains__(self, name: str) -> bool:
        return name in self._controllers

    def __len__(self) -> int:
        return len(self._controllers)

    def add(self, controller: Controller, default: bool = False) -> None:
        """Register `controller` under its target's address.

        Raises:
            SourceClaimedError: Another controller already drives its target
                or regulates its source. Re-adding the same one is a no-op.
        """
        for signal in (controller.target, controller.source):
            holder = self._targets.get(signal) or self._sources.get(signal)
            if holder is not None and holder is not controller:
                raise SourceClaimedError(signal, holder.name)
        self._controllers[controller.name] = controller
        self._targets[controller.target] = controller
        self._sources[controller.source] = controller
        if default or self.default is None:
            self.default = controller.name

    def remove(self, name: str) -> Controller:
        """Detach a controller; its target and source are free for another."""
        try:
            controller = self._controllers.pop(name)
        except KeyError as e:
            raise ControllerNotFoundError(name) from e
        self._targets.pop(controller.target, None)
        self._sources.pop(controller.source, None)
        if self.default == name:
            self.default = next(iter(self._controllers), None)
        return controller

    def find(self, source: Signal) -> Controller | None:
        """The controller regulating `source`, or None. The hot path: most signals have none."""
        return self._sources.get(source)

    def driving(self, target: Signal) -> Controller | None:
        """The controller driving `target`, or None; what refuses a manual demand."""
        return self._targets.get(target)

    def resolve(self, name: str | None = None) -> Controller:
        """By name, or the default. Raises rather than returning None."""
        name = name or self.default
        if name is None:
            raise NoDefaultControllerError()
        try:
            return self._controllers[name]
        except KeyError as e:
            raise ControllerNotFoundError(name) from e

    def source(self, name: str) -> Signal:
        return self.resolve(name).source

    def items(self) -> ItemsView[str, Controller]:
        return self._controllers.items()

    def entries(self) -> Iterator[tuple[Signal, Controller]]:
        """Every controller with the source it regulates."""
        return ((controller.source, controller) for controller in self._controllers.values())

    @property
    def states(self) -> dict[str, ControllerState]:
        return {name: c.state for name, c in self._controllers.items()}

    @property
    def settings(self) -> dict[str, ControllerSettings]:
        return {name: c.settings for name, c in self._controllers.items()}

    @property
    def views(self) -> dict[str, ControllerView]:
        return {name: c.view for name, c in self._controllers.items()}
