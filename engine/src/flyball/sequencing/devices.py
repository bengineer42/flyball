"""Steps that reach a device directly: a demand, or one of its own commands.

Sim-only commands (`fail`, `restore`, `disturb`, ...) are ordinary commands
here too -- they live on the Simulation tab in the UI, not in this vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from flyball.foundation import AddressNotFoundError, NotFoundError, Operator
from flyball.foundation.device import Access, Signal
from flyball.rig import Rig

from .step import Activity, Step


@dataclass(frozen=True)
class Set(Step, tag="set"):
    """Put `values` on `device`'s writable signals, as one demand -- `rig.write` in a step."""

    device: str
    values: dict[str, float]

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        node = rig.resolve(self.device)
        if isinstance(node, Signal):
            raise NotFoundError(f"'{self.device}' is a signal, not a device or namespace")
        rig.write(node, {**self.values}, writer="program")
        return None

    def missing(self, rig: Rig) -> list[str]:
        try:
            node = rig.resolve(self.device)
        except AddressNotFoundError as e:
            return [str(e)]
        if isinstance(node, Signal):
            return [f"'{self.device}' is a signal, not a device or namespace"]
        out: list[str] = []
        for name in self.values:
            try:
                found = node.find(name)
            except AddressNotFoundError as e:
                out.append(str(e))
                continue
            if not isinstance(found, Signal):
                out.append(f"'{found.address}' is a namespace, not a signal")
            elif Access.W not in found.access:
                out.append(f"'{found.address}' [{found.access}] is not writable")
        return out


@dataclass(frozen=True)
class RunCommand(Step, tag="command"):
    """Call one of `device`'s own commands, exactly as `POST /api/devices/{name}/{tag}` would.

    `device_command`, not `command`: every step's wire form reserves `command`
    for its own tag (`"command"`, here), so the device command it should run
    needs a different name.
    """

    locked: ClassVar[bool] = False  # `run_command` takes the rig lock; a long one waits off it

    device_command: str
    device: str
    args: dict[str, Any] | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        try:
            found = rig.devices[self.device]
        except KeyError:
            raise NotFoundError(f"device {self.device!r} not found") from None
        rig.run_command(found, self.device_command, self.args)
        rig.polling.revive(found.name)  # as the HTTP route does: a command that succeeds is the fix
        return None

    def missing(self, rig: Rig) -> list[str]:
        device = rig.devices.get(self.device)
        if device is None:
            return [f"device {self.device!r} is not on the rig"]
        if self.device_command not in device.commands:
            return [f"{self.device!r} has no command {self.device_command!r}"]
        return []


__all__ = ["RunCommand", "Set"]
