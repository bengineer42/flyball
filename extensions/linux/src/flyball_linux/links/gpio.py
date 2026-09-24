"""GPIO over `/dev/gpiochipN` through libgpiod v2."""

from __future__ import annotations

import threading
from typing import Any

from flyball.foundation.config import Config
from flyball.hardware.gpio import GpioLink
from flyball_sim.links import FakeGpio, FakeGpioConfig


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

    def claim_edge(self, line: int, debounce_s: float = 0.0, pull_up: bool | None = None) -> None:
        """Rising-edge detection with libgpiod v2's own debounce -- no polling loop of ours.

        Not run against real hardware; the debounce and edge-event handling
        follow gpiod's documented `LineSettings`/`wait_edge_events` API, not
        a physical sensor.
        """
        from datetime import timedelta

        import gpiod
        from gpiod.line import Bias, Direction, Edge

        bias = Bias.AS_IS if pull_up is None else (Bias.PULL_UP if pull_up else Bias.PULL_DOWN)
        self._request(
            line,
            gpiod.LineSettings(
                direction=Direction.INPUT,
                bias=bias,
                edge_detection=Edge.RISING,
                debounce_period=timedelta(seconds=debounce_s),
            ),
        )

    def count_edges(self, line: int) -> int:
        """Pulses seen on `line` since the last call, draining the kernel's event queue."""
        with self._lock:
            request = self._requests[line]
            count = 0
            while request.wait_edge_events(timeout=0):
                count += len(request.read_edge_events())
            return count

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


class GpioConfig(Config[GpioLink], type="gpio"):
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
