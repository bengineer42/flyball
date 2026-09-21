"""The SPI link protocol: full-duplex byte transfers.

OS-independent -- no bus implementation lives here. `extensions/linux`
supplies `SpidevSpi` (a real `/dev/spidevN.M` bus) and `FakeSpi` (a scripted
one for tests and hardware-free rigs), both built from tagged configs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class SpiLink(Protocol):
    """A full-duplex transfer: as many bytes come back as go out."""

    def transfer(self, data: Sequence[int]) -> bytes: ...


__all__ = ["SpiLink"]
