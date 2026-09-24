"""The GPIO link protocol: claim lines, then set/get/count edges on them.

OS-independent -- no bus implementation lives here. `extensions/linux`
supplies `GpiodChip` (a real `/dev/gpiochipN` chip through libgpiod v2) and
`FakeGpio` (a scripted one for tests and hardware-free rigs), both built
from typed configs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class GpioLink(Protocol):
    """A chip of lines; each is claimed as input or output before use."""

    def claim_output(self, line: int, initial: bool = False) -> None: ...

    def claim_input(self, line: int, pull_up: bool | None = None) -> None: ...

    def set(self, line: int, value: bool) -> None: ...

    def get(self, line: int) -> bool: ...

    def claim_edge(
        self, line: int, debounce_s: float = 0.0, pull_up: bool | None = None
    ) -> None: ...

    def count_edges(self, line: int) -> int: ...


__all__ = ["GpioLink"]
