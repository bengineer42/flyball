"""A step that runs a device's own command: `POST /api/actuators/{name}/{tag}` as a step.

Sim-only commands (`fail`, `restore`, `disturb`, ...) are ordinary commands
here too -- they live on the Simulation tab in the UI, not in this vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flyball.control.loop import LoopMode
from flyball.core import Operator
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.sink import Actuator
from flyball.runtime.rig import Rig

from .command import Activity, Command


@dataclass(frozen=True)
class RunCommand(Command, tag="command"):
    """Call one of `actuator`'s own commands, exactly as the actuator/reader route would.

    `device_command`, not `command`: every step's wire form reserves `command`
    for its own tag (`"command"`, here), so the device command it should run
    needs a different name. `actuator` names any device on the rig -- an
    actuator or a reader, since names are unique rig-wide -- so this also
    reaches a reader's `fail` and `restore`, and a simulated device's commands.
    """

    device_command: str
    actuator: str
    args: dict[str, Any] | None = None

    def run(self, rig: Rig, operator: Operator | None = None) -> Activity | None:
        device = rig.actuators.get(self.actuator) or rig.readers.by_name.get(self.actuator)
        if device is None:
            raise NotFoundError(f"device {self.actuator!r} not found")
        if (
            self.device_command == "demand"
            and self.actuator in rig.loops
            and rig.loops[self.actuator].mode is LoopMode.REGULATING
        ):
            raise ConflictError(
                f"{self.actuator!r} is being regulated by its loop;"
                " stop the loop to drive it by hand"
            )
        try:
            spec = type(device).commands[self.device_command]
        except KeyError as e:
            raise NotFoundError(f"{self.actuator!r} has no command {self.device_command!r}") from e
        spec.method(device, **(self.args or {}))
        if isinstance(device, Actuator):
            rig.apply(device)
        return None

    def missing(self, rig: Rig) -> list[str]:
        device = rig.actuators.get(self.actuator) or rig.readers.by_name.get(self.actuator)
        if device is None:
            return [f"device {self.actuator!r} is not on the rig"]
        if self.device_command not in type(device).commands:
            return [f"{self.actuator!r} has no command {self.device_command!r}"]
        return []


__all__ = ["RunCommand"]
