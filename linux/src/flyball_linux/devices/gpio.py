"""A GPIO line as a device: an output switched by a demand, or an input read as 0/1.

`direction: output` (the default) declares one `[W]` signal `on`, 0 or 1,
which `commit` drives onto the line -- a relay, a valve, a fan -- and the
commands `on`/`off` for a hand on the switch. `direction: input` declares
one `[RP]` signal `level` instead: a door switch, a float switch.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from flyball.core.config import resolve
from flyball.core.device import Device, DeviceState, DriverConfig, command
from flyball.core.errors import ConflictError
from flyball.core.quantity import Quantity
from flyball.core.signal import Access, Node, Sample, Signal, SignalSpec, WriteState
from flyball.core.units.si import One
from pydantic import Field

from flyball_linux.links.gpio import GpioLink, GpioLinkConfig

Direction = Literal["output", "input"]

ON = Quantity("on", One)
LEVEL = Quantity("level", One)


@dataclass(frozen=True, slots=True, kw_only=True)
class GpioLineState(DeviceState):
    level: bool | None = None
    """The line's logical level: what an output was last driven to, or an input last read."""


class GpioLine(Device):
    """One line, claimed as an output or an input on construction.

    `invert` is for an active-low relay board or a pulled-up switch: the
    logical level the rig sees is the electrical one flipped.
    """

    def __init__(
        self,
        name: str,
        link: GpioLink,
        line: int,
        direction: Direction = "output",
        invert: bool = False,
        initial: bool = False,
        pull_up: bool | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.line = line
        self.direction: Direction = direction
        self.invert = invert
        self.initial = initial
        self.pull_up = pull_up
        self._level: bool | None = None
        if direction == "output":
            self.bind((SignalSpec(name="on", quantity=ON, access=Access.W, limits=(0.0, 1.0)),))
            link.claim_output(line, initial != invert)
            self._level = initial
        else:
            self.bind((
                SignalSpec(
                    name="level", quantity=LEVEL, access=Access.RP, range=(0.0, 1.0), precision=0
                ),
            ))
            link.claim_input(line, pull_up)

    @property
    def config(self) -> GpioLineConfig:
        return GpioLineConfig(
            link="",
            line=self.line,
            direction=self.direction,
            invert=self.invert,
            initial=self.initial,
            pull_up=self.pull_up,
        )

    @property
    def state(self) -> GpioLineState:
        return GpioLineState(level=self._level)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """An input's level, 0 or 1; an output has nothing to read."""
        if self.direction != "input":
            return
        self._level = self.link.get(self.line) != self.invert
        yield Sample(self.root, time_ns, {self.signals["level"]: float(self._level)})

    def _drive(self, on: bool) -> None:
        if self.direction != "output":
            raise ConflictError(f"{self.name} is an input line; it cannot be driven")
        self._level = on
        self.link.set(self.line, on != self.invert)

    def commit(self, time_ns: int) -> Mapping[Signal, WriteState]:
        """Drive the line; a switch has two positions and neither is a rail, so no `at_limit`."""
        states: dict[Signal, WriteState] = {}
        for signal, value in self.pending.items():
            on = value >= 0.5
            self._drive(on)
            states[signal] = WriteState(value=float(on))
        self.written.update(states)
        self.pending.clear()
        return states

    @command
    def on(self) -> GpioLineState:
        """Switch the line on, whatever was last demanded."""
        self._drive(True)
        return self.state

    @command
    def off(self) -> GpioLineState:
        """Switch the line off, whatever was last demanded."""
        self._drive(False)
        return self.state


class GpioLineConfig(DriverConfig[GpioLine], tag="gpio_line"):
    """`driver: gpio_line`: `{ link, line }`, or `pin: GPIO18` from the board profile."""

    link: GpioLinkConfig | str  # type: ignore[valid-type]
    line: int = Field(ge=0)
    direction: Direction = "output"
    invert: bool = Field(default=False, description="An active-low relay board or switch.")
    initial: bool = Field(default=False, description="An output's level at startup.")
    pull_up: bool | None = Field(
        default=None, description="An input's bias: true pulls up, false down, omitted as-is."
    )

    def build(self, name: str, label: str | None = None) -> GpioLine:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return GpioLine(
            name,
            resolve(self.link),
            self.line,
            self.direction,
            self.invert,
            self.initial,
            self.pull_up,
            label=label,
        )


GpioLine.config_type = GpioLineConfig  # the config is declared after the device it builds


__all__ = ["Direction", "GpioLine", "GpioLineConfig", "GpioLineState"]
