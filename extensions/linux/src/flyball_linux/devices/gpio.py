"""A GPIO line as a device: an output switched by a demand, or an input read as 0/1.

`direction: output` (the default) declares one `[RPW]` demand `on`, 0 or 1,
which `commit` drives onto the line -- a relay, a valve, a fan -- and the
commands `on`/`off` for a hand on the switch. `direction: input` declares
one `[RP]` signal `level` instead: a door switch, a float switch.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from flyball.foundation.config import resolve
from flyball.foundation.device import (
    Access,
    Committable,
    DriverConfig,
    Node,
    Readable,
    Role,
    Sample,
    SignalSpec,
    command,
)
from flyball.foundation.errors import ConflictError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import One
from pydantic import Field

from flyball_linux.links.gpio import GpioLink, GpioLinkConfig

Direction = Literal["output", "input"]

ON = Quantity("on", One)
LEVEL = Quantity("level", One)


class GpioLine(Readable, Committable):
    """One line, claimed as an output or an input on construction.

    `invert` is for an active-low relay board or a pulled-up switch: the
    logical level the rig sees is the electrical one flipped.

    An output's `on` declares `off` at 0, what a stop writes -- except with `invert:
    true`, where whether logical 0 is the load's off depends on why it was inverted,
    so it declares none and a stop leaves it as it is. A direction or select line
    that must hold through a stop says `stop: {on: keep}`.
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
        if direction == "output":
            self.bind((
                SignalSpec(
                    name="on",
                    quantity=ON,
                    access=Access.RPW,
                    role=Role.DEMAND,
                    limits=(0.0, 1.0),
                    initial=float(initial),
                    off=None if invert else 0.0,
                ),
            ))
            link.claim_output(line, initial != invert)
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

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """An input's level, 0 or 1; an output has nothing to read."""
        if self.direction != "input":
            return
        level = self.link.get(self.line) != self.invert
        yield Sample(self.root, time_ns, {self.signals["level"]: float(level)})

    def _drive(self, on: bool) -> None:
        if self.direction != "output":
            raise ConflictError(f"{self.name} is an input line; it cannot be driven")
        self.link.set(self.line, on != self.invert)

    def commit(self, time_ns: int) -> None:
        """Drive the line; a switch has two positions and neither is a rail, so no `at_limit`."""
        for signal, value in self.staged.items():
            on = value >= 0.5
            self._drive(on)
            readback = float(on)
            if readback != value:
                signal.push(readback, time_ns)

    @command(writes=("on",))
    def on(self) -> None:
        """Switch the line on, whatever was last demanded; refused while a controller drives it."""
        self._drive(True)
        self.signals["on"].push(1.0)

    @command(writes=("on",))
    def off(self) -> None:
        """Switch the line off, whatever was last demanded; refused while a controller drives
        it."""
        self._drive(False)
        self.signals["on"].push(0.0)


class GpioLineConfig(DriverConfig[GpioLine], type="gpio_line"):
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


__all__ = ["Direction", "GpioLine", "GpioLineConfig"]
