"""The rig's controllers: by output address for people and programs, by measured signal to tick.

A controller is named by the demand it drives, its output, so its name is
that signal's address (`"heaters.heater1"`). One controller per output and
one per measured signal: two on an output would fight over it, two on a
measured signal would both step on the same reading.
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterator

from flyball.foundation.device import Signal
from flyball.foundation.errors import ConflictError, NotFoundError, NotReadyError
from flyball.model.controller import Controller, ControllerSpec, ControllerState, ControllerView


class ControllerNotFoundError(NotFoundError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Controller {name!r} not found")


class NoDefaultControllerError(NotReadyError):
    def __init__(self) -> None:
        super().__init__("No default controller set")


class SignalClaimedError(ConflictError):
    """A signal already has a controller on it: as its measured signal, or as its output.

    A controller's name is its output's address, so a claimed output reads
    "driven by" and a claimed measured signal "regulated by".
    """

    def __init__(self, signal: Signal, by: str) -> None:
        verb = "driven" if signal.address == by else "regulated"
        super().__init__(f"{signal.address} is already {verb} by controller {by!r}")


class Controllers:
    """The rig's controllers, keyed by output address; `find` by measured signal is the hot path."""

    _controllers: dict[str, Controller]
    _measured: dict[Signal, Controller]
    _outputs: dict[Signal, Controller]
    default: str | None = None

    def __init__(self) -> None:
        self._controllers = {}
        self._measured = {}
        self._outputs = {}

    def __getitem__(self, name: str) -> Controller:
        return self._controllers[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._controllers)

    def __contains__(self, name: str) -> bool:
        return name in self._controllers

    def __len__(self) -> int:
        return len(self._controllers)

    def add(self, controller: Controller, default: bool = False) -> None:
        """Register `controller` under its output's address.

        Raises:
            SignalClaimedError: Another controller already drives its output
                or regulates its measured signal. Re-adding the same one is a no-op.
        """
        for signal in (controller.output_signal, controller.measured_signal):
            holder = self._outputs.get(signal) or self._measured.get(signal)
            if holder is not None and holder is not controller:
                raise SignalClaimedError(signal, holder.name)
        self._controllers[controller.name] = controller
        self._outputs[controller.output_signal] = controller
        self._measured[controller.measured_signal] = controller
        if default or self.default is None:
            self.default = controller.name

    def remove(self, name: str) -> Controller:
        """Detach a controller; its output and measured signal are free for another."""
        try:
            controller = self._controllers.pop(name)
        except KeyError as e:
            raise ControllerNotFoundError(name) from e
        self._outputs.pop(controller.output_signal, None)
        self._measured.pop(controller.measured_signal, None)
        if self.default == name:
            self.default = next(iter(self._controllers), None)
        return controller

    def find(self, measured: Signal) -> Controller | None:
        """The controller regulating `measured`, or None. The hot path: most signals have none."""
        return self._measured.get(measured)

    def driving(self, output: Signal) -> Controller | None:
        """The controller driving `output`, or None; what refuses a manual demand."""
        return self._outputs.get(output)

    def resolve(self, name: str | None = None) -> Controller:
        """By name, or the default. Raises rather than returning None."""
        name = name or self.default
        if name is None:
            raise NoDefaultControllerError()
        try:
            return self._controllers[name]
        except KeyError as e:
            raise ControllerNotFoundError(name) from e

    def measured(self, name: str) -> Signal:
        return self.resolve(name).measured_signal

    def items(self) -> ItemsView[str, Controller]:
        return self._controllers.items()

    def entries(self) -> Iterator[tuple[Signal, Controller]]:
        """Every controller with the measured signal it regulates."""
        return ((c.measured_signal, c) for c in self._controllers.values())

    @property
    def states(self) -> dict[str, ControllerState]:
        return {name: c.state for name, c in self._controllers.items()}

    @property
    def specs(self) -> dict[str, ControllerSpec]:
        return {name: c.spec for name, c in self._controllers.items()}

    @property
    def views(self) -> dict[str, ControllerView]:
        return {name: c.view for name, c in self._controllers.items()}
