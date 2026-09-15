"""GPIO over `/dev/gpiochipN` through libgpiod v2."""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

from flyball.core.config import Config
from pydantic import Field


@runtime_checkable
class GpioLink(Protocol):
    """A chip of lines; each is claimed as input or output before use."""

    def claim_output(self, line: int, initial: bool = False) -> None: ...

    def claim_input(self, line: int, pull_up: bool | None = None) -> None: ...

    def set(self, line: int, value: bool) -> None: ...

    def get(self, line: int) -> bool: ...


class FakeGpio:
    """Lines as a dict; sets are kept in order. Reading an unclaimed line is an error."""

    def __init__(self, levels: dict[int, bool] | None = None) -> None:
        self.levels = dict(levels or {})
        self.claimed: dict[int, str] = {}
        self.sets: list[tuple[int, bool]] = []

    def claim_output(self, line: int, initial: bool = False) -> None:
        self.claimed[line] = "output"
        self.levels[line] = initial

    def claim_input(self, line: int, pull_up: bool | None = None) -> None:
        self.claimed[line] = "input"
        self.levels.setdefault(line, bool(pull_up))

    def set(self, line: int, value: bool) -> None:
        if self.claimed.get(line) != "output":
            raise OSError(f"line {line} is not claimed as an output")
        self.levels[line] = value
        self.sets.append((line, value))

    def get(self, line: int) -> bool:
        if line not in self.claimed:
            raise OSError(f"line {line} is not claimed")
        return self.levels[line]


class FakeGpioConfig(Config[GpioLink], tag="fake_gpio"):
    levels: dict[int, bool] = Field(default_factory=dict, description="Input levels by line.")

    def build(self) -> GpioLink:
        return FakeGpio(self.levels)


class GpiodChip:
    """`/dev/<chip>` through gpiod v2. Needs the `gpio` extra.

    Lines are requested one at a time as they are claimed, with the consumer
    name `flyball`, so `gpioinfo` shows who holds them.
    """

    def __init__(self, chip: str = "gpiochip0") -> None:
        import gpiod

        self.chip = chip
        self._chip = gpiod.Chip(f"/dev/{chip}" if not chip.startswith("/") else chip)
        self._requests: dict[int, Any] = {}
        self._lock = threading.Lock()

    def _request(self, line: int, settings: Any) -> None:
        with self._lock:
            if line in self._requests:
                self._requests[line].release()
            self._requests[line] = self._chip.request_lines(
                consumer="flyball", config={line: settings}
            )

    def claim_output(self, line: int, initial: bool = False) -> None:
        import gpiod
        from gpiod.line import Direction, Value

        self._request(
            line,
            gpiod.LineSettings(
                direction=Direction.OUTPUT, output_value=Value.ACTIVE if initial else Value.INACTIVE
            ),
        )

    def claim_input(self, line: int, pull_up: bool | None = None) -> None:
        import gpiod
        from gpiod.line import Bias, Direction

        bias = Bias.AS_IS if pull_up is None else (Bias.PULL_UP if pull_up else Bias.PULL_DOWN)
        self._request(line, gpiod.LineSettings(direction=Direction.INPUT, bias=bias))

    def set(self, line: int, value: bool) -> None:
        from gpiod.line import Value

        with self._lock:
            self._requests[line].set_value(line, Value.ACTIVE if value else Value.INACTIVE)

    def get(self, line: int) -> bool:
        from gpiod.line import Value

        with self._lock:
            return self._requests[line].get_value(line) == Value.ACTIVE

    def close(self) -> None:
        with self._lock:
            for request in self._requests.values():
                request.release()
            self._requests.clear()
            self._chip.close()


class GpioConfig(Config[GpioLink], tag="gpio"):
    """A kernel GPIO chip: `chip = "gpiochip4"` is `/dev/gpiochip4`."""

    chip: str = "gpiochip0"

    def build(self) -> GpioLink:
        return GpiodChip(self.chip)


GPIO_LINKS = (FakeGpioConfig, GpioConfig)
GpioLinkConfig = Config.union(*GPIO_LINKS)

__all__ = [
    "GPIO_LINKS",
    "FakeGpio",
    "FakeGpioConfig",
    "GpioConfig",
    "GpioLink",
    "GpioLinkConfig",
    "GpiodChip",
]
