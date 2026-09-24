"""A criterion: a test on one signal's value -- above, below, or near a number.

What a program's `settle` waits for when it names a signal
(`settle: {signal: furnace.sample, below: 60}`), and the shape a rule's
`when:`, a value trip and a permissive are meant to take too, so there is one
way to write "this signal is past that value". It is only the test: how many
readings running, how long to wait, and what to do once it is met belong to
whoever holds it.

A reading with no value never passes by accident. Only a fault (`invalid`,
`stale`) can meet a criterion, and only when `on_no_value` is `fire`: a trip
wants a dead sensor to trip, and a wait wants it not to count. `pending` and
`not_applicable` never meet one. The holder chooses the default; the
criterion's own `on_no_value` overrides it.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flyball.foundation.device import NoValue, OnNoValue, Reading, Value


class Criterion(BaseModel):
    """A test on one signal: `above`, `below`, or `near` with `within`; exactly one of them.

    `above` and `below` are strict: `above: 60` is not met at 60. `near: 700,
    within: 5` is met from 695 to 705 inclusive. With `from_start`, the number
    is added to the signal's value when the holder started (a step's start):
    `above: 36, from_start: true` is 36 more than it was.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal: str = Field(description="The address of the signal to test.")
    above: float | None = Field(
        default=None, description="Met while the value is above this (not at it)."
    )
    below: float | None = Field(
        default=None, description="Met while the value is below this (not at it)."
    )
    near: float | None = Field(
        default=None, description="Met while the value is within `within` of this."
    )
    within: float | None = Field(
        default=None, description="How close to `near` counts, either side; only with `near`."
    )
    from_start: bool = Field(
        default=False,
        description="The number is relative to the signal's value at the start: "
        "`above: 36` means 36 more than it was.",
    )
    on_no_value: OnNoValue | None = Field(
        default=None,
        description="A reading with no value because of a fault (`invalid`, `stale`): `fire` "
        "meets the criterion, `ignore` does not. Unset, whoever holds the criterion decides "
        "(`settle`: `ignore`). `pending` and `not_applicable` never meet it.",
    )

    @model_validator(mode="after")
    def _one_test(self) -> Criterion:
        given = [name for name in ("above", "below", "near") if getattr(self, name) is not None]
        if len(given) != 1:
            raise ValueError(
                f"a criterion on {self.signal!r} gives exactly one of `above`, `below` or "
                f"`near` (with `within`); found {given or 'none'}"
            )
        if self.near is None and self.within is not None:
            raise ValueError(f"criterion on {self.signal!r}: `within` goes with `near`")
        if self.near is not None and self.within is None:
            raise ValueError(f"criterion on {self.signal!r}: `near` needs `within`")
        for name in ("above", "below", "near", "within"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"criterion on {self.signal!r}: {name} {value!r} is not finite")
        if self.within is not None and self.within < 0:
            raise ValueError(f"criterion on {self.signal!r}: within {self.within!r} is negative")
        return self

    def passes(self, value: float, base: float = 0.0) -> bool:
        """Whether a number passes the test; `base` is the value at the start, with `from_start`."""
        offset = base if self.from_start else 0.0
        if self.above is not None:
            return value > self.above + offset
        if self.below is not None:
            return value < self.below + offset
        assert self.near is not None and self.within is not None
        return abs(value - (self.near + offset)) <= self.within

    def met(
        self,
        reading: Reading | None,
        base: float = 0.0,
        default: OnNoValue = OnNoValue.IGNORE,
    ) -> bool:
        """Whether `reading` meets it.

        None (nothing read yet), `pending` and `not_applicable` never do; a
        fault (`invalid`, `stale`) does only under `on_no_value: fire`
        (`default` when the criterion leaves it unset). A value that is not
        a number (a mode, a record) never does.

        Args:
            reading: The signal's newest reading.
            base: The signal's value at the start, for `from_start`.
            default: What a fault means when `on_no_value` is unset: the
                holder's choice.
        """
        if reading is None:
            return False
        value: Value = reading.value
        if isinstance(value, NoValue):
            return value.quality.fault and (self.on_no_value or default) is OnNoValue.FIRE
        if not isinstance(value, (int, float)):
            return False
        return self.passes(float(value), base)

    def describe(self) -> str:
        """The test in words: `furnace.sample below 60`, `scale.mass above its start +36`."""

        def number(value: float) -> str:
            return f"its start {value:+g}" if self.from_start else f"{value:g}"

        if self.above is not None:
            return f"{self.signal} above {number(self.above)}"
        if self.below is not None:
            return f"{self.signal} below {number(self.below)}"
        assert self.near is not None and self.within is not None
        return f"{self.signal} within {self.within:g} of {number(self.near)}"


__all__ = ["Criterion"]
