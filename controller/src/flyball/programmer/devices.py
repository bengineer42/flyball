"""Steps that reach a device directly: a demand, or one of its own commands.

Sim-only commands (`fail`, `restore`, `disturb`, ...) are ordinary commands
here too -- they live on the Simulation tab in the UI, not in this vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flyball.core import NotFoundError, Operator
from flyball.core.signal import Signal
from flyball.runtime.rig import Rig

from .command import Activity, Command


@dataclass(frozen=True)
class Set(Command, tag="set"):
    """Put `values` on `device`'s writable signals, as one demand -- `rig.demand` in a step."""

    device: str
    values: dict[str, float]

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        node = rig.resolve(self.device)
        if isinstance(node, Signal):
            raise NotFoundError(f"'{self.device}' is a signal, not a device or namespace")
        rig.demand(node, {**self.values})
        return None


@dataclass(frozen=True)
class RunCommand(Command, tag="command"):
    """Call one of `device`'s own commands, exactly as `POST /api/devices/{name}/{tag}` would.

    `device_command`, not `command`: every step's wire form reserves `command`
    for its own tag (`"command"`, here), so the device command it should run
    needs a different name.
    """

    device_command: str
    device: str
    args: dict[str, Any] | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        try:
            found = rig.devices[self.device]
        except KeyError:
            raise NotFoundError(f"device {self.device!r} not found") from None
        try:
            spec = type(found).commands[self.device_command]
        except KeyError as e:
            raise NotFoundError(f"{self.device!r} has no command {self.device_command!r}") from e
        spec.method(found, **(self.args or {}))
        return None


__all__ = ["RunCommand", "Set"]
