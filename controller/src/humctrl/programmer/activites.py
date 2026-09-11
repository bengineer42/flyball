from __future__ import annotations

from collections.abc import Callable

from humctrl.control import Loop
from humctrl.core import Positive, Reading, Signal

from .command import LoopActivity


class Sustained(LoopActivity):
    def __init__(
        self,
        test: Callable[[Reading], bool],
        timeout: Positive | None = None,
    ) -> None:
        self.signal = Signal(timeout)
        self.test = test

    def tick(self, loop: Loop, reading: Reading) -> None:
        if self.test(reading):
            self.finish()
