from __future__ import annotations

from collections.abc import Callable
from typing import Any

from humctrl.core import Positive, Reading, Signal

from .command import Activity


class Sustained(Activity):
    def __init__(
        self,
        test: Callable[[Reading], bool],
        timeout: Positive | None = None,
    ) -> None:
        self.signal = Signal(timeout)
        self.test = test

    def tick(self, rig: Any, reading: Reading) -> None:
        if self.test(reading):
            self.finish()
