"""Feedforwards: the open-loop guess at a demand for a setpoint; the law corrects the rest.

A loop's demand is `feedforward(setpoint) + correction`. The feedforward maps
the channel's unit to the actuator's: the identity when they agree (a demand
of "50 °C" to a controller that takes °C), a static model of the plant when
they do not (the drive that holds 50 °C in this furnace). Subclassing
generates `config` from `__init__` and registers the tag, exactly as
[ControlLaw][flyball.control.types.ControlLaw] does.
"""

from __future__ import annotations

from bisect import bisect_left
from inspect import signature
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from flyball.core.model import ModelOf, creation_model


class FeedforwardConfig(BaseModel):
    """How a feedforward was specified: its constructor arguments and its tag."""

    model_config = ConfigDict(extra="forbid")

    feedforward: ClassVar[type]
    init_names: ClassVar[tuple[str, ...]] = ()

    tag: str

    def build(self) -> Any:
        return self.feedforward(**{name: getattr(self, name) for name in self.init_names})


Feedforwards: dict[str, type[Feedforward]] = {}


class Feedforward:
    """Base for feedforwards. `Sub.config` is the model; `sub.config` its values."""

    tag: ClassVar[str] = None  # pyright: ignore[reportAssignmentType]
    config: ClassVar[Any] = None

    def __init_subclass__(
        cls, tag: str | None = None, register: bool = True, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or cls.__name__
        if "config" not in cls.__dict__:
            model = creation_model(
                cls,
                suffix="Config",
                base=FeedforwardConfig,
                extra={"tag": (Literal[cls.tag], cls.tag)},
            )
            model.feedforward = cls  # pyright: ignore[reportAttributeAccessIssue]
            model.init_names = tuple(signature(cls).parameters)  # pyright: ignore[reportAttributeAccessIssue]
            cls.config = ModelOf(model, tuple(model.model_fields))
        if register:
            if cls.tag in Feedforwards:
                raise ValueError(f"Feedforward with tag '{cls.tag}' is already registered.")
            Feedforwards[cls.tag] = cls

    def __call__(self, setpoint: float) -> float:
        """The demand, in the actuator's unit, that ought to hold `setpoint`."""
        raise NotImplementedError


class Setpoint(Feedforward, tag="setpoint"):
    """Demand equals setpoint: the actuator takes the channel's unit."""

    def __call__(self, setpoint: float) -> float:
        return setpoint


class NoFeedforward(Feedforward, tag="none"):
    """The law does all the work: a bare power actuator under PID."""

    def __call__(self, setpoint: float) -> float:
        return 0.0


class Affine(Feedforward, tag="affine"):
    """`demand = gain * setpoint + bias`: the two-number model that fits most plants nearby."""

    def __init__(self, gain: float, bias: float = 0.0) -> None:
        self.gain = gain
        self.bias = bias

    def __call__(self, setpoint: float) -> float:
        return self.gain * setpoint + self.bias


class Table(Feedforward, tag="table"):
    """Piecewise-linear `(setpoint, demand)` breakpoints: a static curve measured on the rig.

    Held flat beyond the ends.
    """

    def __init__(self, points: list[tuple[float, float]]) -> None:
        if not points:
            raise ValueError("at least one point")
        self.points = sorted(points)
        self._x = [x for x, _ in self.points]

    def __call__(self, setpoint: float) -> float:
        i = bisect_left(self._x, setpoint)
        if i == 0:
            return self.points[0][1]
        if i == len(self.points):
            return self.points[-1][1]
        (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (setpoint - x0) / (x1 - x0)


type FeedforwardLike = Feedforward | FeedforwardConfig
