"""A controller: one publishing signal regulated through one writable signal.

Today's [Loop][flyball.control.loop.Loop] keyed by its target -- a W signal
has at most one controller, so the controller is named by that signal's
address (`"heaters.heater1"`). The feedforward maps the source's unit to
the target's, exactly as it maps a channel's unit to `demand_unit` today.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flyball.core import Clock, ConflictError, Reading, Signal, WriteState
from flyball.core.signal import Access

from .feedforward import Feedforward, FeedforwardConfig, NoFeedforward, Setpoint
from .loop import Loop
from .types import ControlLaw, ControlLawConfig, ControlLawView, Tuning


class Controller(Loop[Any]):
    """Binds `source` (a P signal) to `target` (a W signal) through a law and a feedforward.

    `write` is how a demand reaches the target: the rig injects one that
    calls its `demand()` and returns the committed value, or None when the
    commit is deferred to the end of the delivery -- then
    [delivered][flyball.control.controller.Controller.delivered] closes the
    tick with the write state. Until it is injected, a demand is recorded
    and nothing is written.
    """

    target: Signal
    source: Signal

    def __init__(
        self,
        clock: Clock,
        target: Signal,
        source: Signal,
        *,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning | None = None,
        feedforward: Feedforward | FeedforwardConfig | None = None,
        min_period_s: float | None = None,
        write: Callable[[float], float | None] | None = None,
    ) -> None:
        if Access.W not in target.access:
            raise ConflictError(f"{target.address} [{target.access}] is not writable")
        if Access.P not in source.access:
            raise ConflictError(f"{source.address} [{source.access}] is not publishing")
        self.target = target
        self.source = source
        same_unit = source.unit == target.unit
        if isinstance(feedforward, FeedforwardConfig):
            feedforward = feedforward.build()
        if feedforward is None:
            feedforward = Setpoint() if same_unit else NoFeedforward()
        elif isinstance(feedforward, Setpoint) and not same_unit:
            # Handing the target demands in the source's unit when it takes
            # another would run happily and do nonsense.
            raise ConflictError(
                f"controller on {source.address} ({source.unit}) cannot pass its setpoint to"
                f" {target.address!r}, which takes demands in {target.unit}"
            )
        super().__init__(
            clock,
            law=law,
            min_period_s=min_period_s,
            write=self._unwired if write is None else write,
            feedforward=feedforward,
            name=target.address,
            demand_unit=target.unit,
        )

    @staticmethod
    def _unwired(demand: float) -> float | None:
        """Nothing to write to yet: the demand is recorded on the controller, not delivered."""
        return None

    def on_reading(self, reading: Reading) -> None:
        """The source signal's node delivered a sample; step the law on its reading."""
        assert reading.signal is self.source, f"{reading.signal} is not {self.source}"
        self.tick(reading)

    def delivered(self, state: WriteState) -> None:
        """The deferred commit reported what the target was set to.

        Records `expected` and `delivered_correction` as `_apply_demand`
        would have, had `write` returned the value at once.
        """
        self.expected = state.value
        self.delivered_correction = (
            None if state.value is None or self._base is None else state.value - self._base
        )
