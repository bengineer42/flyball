"""`Feedforward`: the base a feedforward subclasses, and the schema it generates by doing so.

`control/feedforward.py` holds the concrete feedforwards that ship (`Setpoint`,
`Affine`, `Table`, ...); this is just the machinery every one of them
subclasses, the same shape [ControlLaw][flyball.model.law.ControlLaw] gives laws.
"""

from __future__ import annotations

import builtins
from inspect import signature
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from flyball.model.errors import FeedforwardNotInvertibleError
from flyball.model.model import ModelOf, creation_model


class FeedforwardConfig(BaseModel):
    """How a feedforward was specified: its constructor arguments and its type."""

    model_config = ConfigDict(extra="forbid")

    feedforward: ClassVar[builtins.type]
    init_names: ClassVar[tuple[str, ...]] = ()

    type: str

    def build(self) -> Any:
        return self.feedforward(**{name: getattr(self, name) for name in self.init_names})


class Feedforward:
    """Base for feedforwards. `Sub.config` is the model; `sub.config` its values."""

    type: ClassVar[str] = None  # pyright: ignore[reportAssignmentType]
    config: ClassVar[Any] = None

    def __init_subclass__(cls, type: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.type = type or cls.__dict__.get("type") or cls.__name__
        if "config" not in cls.__dict__:
            model = creation_model(
                cls,
                suffix="Config",
                base=FeedforwardConfig,
                extra={"type": (Literal[cls.type], cls.type)},
            )
            model.feedforward = cls  # pyright: ignore[reportAttributeAccessIssue]
            model.init_names = tuple(signature(cls).parameters)  # pyright: ignore[reportAttributeAccessIssue]
            cls.config = ModelOf(model, tuple(model.model_fields))

    def __call__(self, setpoint: float, rate: float = 0.0) -> float:
        """The demand, in the actuator's unit, that ought to hold `setpoint`.

        `rate` is the setpoint's own rate of change, per second in the
        channel's unit; 0 outside a ramp. A feedforward that ignores it is
        free to.
        """
        raise NotImplementedError

    def invert(self, demand: float, rate: float = 0.0) -> float:
        """The setpoint (channel unit) whose demand is `demand` at this `rate`.

        Raises:
            FeedforwardNotInvertibleError: This feedforward has no inverse
                (several setpoints share a demand, or it ignores the
                setpoint entirely).
        """
        raise FeedforwardNotInvertibleError(self.type)


class Setpoint(Feedforward, type="setpoint"):
    """Demand equals setpoint: the actuator takes the channel's unit.

    No `rate_gain`: the actuator already takes the channel's own unit, so a
    rate term here would be a lead compensator, not the plant-capacity model
    `affine`/`table` add one for. Out of scope until something needs it.

    The default feedforward `Controller` picks when none is given and the
    source and target share a unit -- lives here, not `control/feedforward.py`,
    so `Controller` (which needs it as a fallback) doesn't have to import
    upward from `model` into `control`.
    """

    def __call__(self, setpoint: float, rate: float = 0.0) -> float:
        return setpoint

    def invert(self, demand: float, rate: float = 0.0) -> float:
        return demand


class NoFeedforward(Feedforward, type="none"):
    """The law does all the work: a bare power actuator under PID.

    The default `Controller` picks when the source and target units differ
    and none is given -- see `Setpoint`'s docstring for why it lives here.
    """

    def __call__(self, setpoint: float, rate: float = 0.0) -> float:
        return 0.0

    # No inverse: every setpoint gives the same demand (0), so a demand does
    # not identify one. Falls through to the base's error.


type FeedforwardLike = Feedforward | FeedforwardConfig
