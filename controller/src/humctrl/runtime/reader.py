from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from humctrl.core.reading import Channel, Reader
from humctrl.core.utils import PeriodicLoop

if TYPE_CHECKING:
    from humctrl.runtime.rig import Rig


class Readers:
    periodic: dict[Reader, PeriodicLoop]

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.periodic = {}

    def start_periodic(self, reader: Reader, period: float) -> None:
        if reader in self.periodic:
            self.periodic[reader].stop()
            self.periodic.pop(reader, None)
        self.periodic[reader] = PeriodicLoop(self.rig.read, period, False, reader)
        self.periodic[reader].start()

    def stop_all(self) -> None:
        for loop in self.periodic.values():
            loop.stop()

    @property
    def channels(self) -> Sequence[Channel]:
        return [ch for reader in self.periodic for ch in reader.channels]
