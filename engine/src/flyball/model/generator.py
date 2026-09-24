"""`SetpointGenerator`: the base a trajectory subclasses, and the schema it generates by doing so.

`control/setpoint.py` holds the concrete generators that ship (`Dwell`,
`LinearRampSetpoint`, `Profile`, ...) and the discriminated union over them
(`GeneratorConfig`, which also admits whatever the current `Catalogs`
registered); this is just the machinery every one subclasses, the same shape
[ControlLaw][flyball.model.law.ControlLaw] gives laws.
"""

from __future__ import annotations

import builtins
from inspect import signature
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic_core import core_schema

from flyball.model.model import ModelOf, creation_model


class SetpointGeneratorConfig(BaseModel):
    """How a generator was specified: its constructor arguments and its type.

    `type` is declared on the base so the base has a schema; each subclass
    narrows it to a `Literal`, which lets a union of configs discriminate on
    it -- the same shape [ControlLawConfig][flyball.model.law.ControlLawConfig]
    gives laws.
    """

    # A NaN or infinite end, value or rate would carry straight into the setpoint.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    generator: ClassVar[builtins.type[SetpointGenerator]]
    init_names: ClassVar[tuple[str, ...]] = ()

    type: str

    def build(self) -> SetpointGenerator:
        """A fresh generator, not yet started."""
        return self.generator(**{name: getattr(self, name) for name in self.init_names})


class SetpointGenerator:
    """A reference trajectory. Subclassing derives `config`; registering is explicit.

    A type is required when subclassed (`class Dwell(SetpointGenerator, type="dwell")`);
    a subclass that omits it raises at class creation. Nothing is written into
    a shared registry any more -- see [Catalogs][flyball.model.catalog.Catalogs].
    """

    type: ClassVar[str] = ""
    config: ClassVar[Any] = None
    view_fields: ClassVar[tuple[str, ...]] = ()
    """Attributes beyond the constructor's that a running instance shows on the wire."""
    end_time: float | None = None
    """When the trajectory lands, in the time `start` was given; None until started, or endless."""

    def __init_subclass__(cls, type: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        resolved = type or cls.__dict__.get("type")
        if not resolved:
            raise TypeError(
                f"{cls.__name__} must declare a type, e.g. "
                f"class {cls.__name__}(SetpointGenerator, type=...)"
            )
        cls.type = resolved

        # Every generator gets its own config, derived from `__init__`: the
        # field list a form needs to build one, plus the type that names it.
        if "config" not in cls.__dict__:
            config_model = creation_model(
                cls,
                suffix="Config",
                base=SetpointGeneratorConfig,
                extra={"type": (Literal[cls.type], cls.type)},
            )
            config_model.generator = cls  # pyright: ignore[reportAttributeAccessIssue]
            config_model.init_names = tuple(signature(cls).parameters)  # pyright: ignore[reportAttributeAccessIssue]
            cls.config = ModelOf(config_model, tuple(config_model.model_fields))

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        """Serialise as `config` plus any `view_fields`: a view, not a way to build one."""
        return core_schema.json_or_python_schema(
            json_schema=core_schema.no_info_plain_validator_function(cls._reject),
            python_schema=core_schema.is_instance_schema(cls),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda g: g.wire(), when_used="always"
            ),
        )

    @staticmethod
    def _reject(value: Any) -> Any:
        raise ValueError("a running trajectory cannot be built from the wire")

    def wire(self) -> dict[str, Any]:
        """`{"type": ..., **the constructor's arguments, **view_fields present so far}`."""
        data = self.config.model_dump(mode="json")
        for name in type(self).view_fields:
            value = getattr(self, name, None)
            if value is not None:
                data[name] = value
        return data

    @property
    def bounded(self) -> bool:
        """Whether the trajectory ends of its own accord, whatever it starts from."""
        return True

    def start(self, time: float, value: float) -> None:
        """Bind to the rig: `time` is the origin, `value` the process value then."""

    def reseed(self, time: float, value: float) -> bool:
        """Go on from `value` at `time`, at the trajectory's own rate: whether anything changed.

        What a controller resuming after a hold calls, so the setpoint does not jump to
        where the trajectory's clock took it meanwhile. Default: nothing (a dwell holds its
        value whatever the reading).
        """
        return False

    def generate(self, time: float) -> float:
        """The setpoint at `time`."""
        raise NotImplementedError

    def finished(self, time: float) -> bool:
        """Whether the trajectory has landed by `time` and nothing further will change.

        In the controller's time, as `generate` and `rate` take it, so a
        scaled or stepped clock is judged where it stands rather than by a
        wall-clock timer per trajectory.
        """
        return False

    def rate(self, time: float) -> float:
        """How fast the setpoint is moving at `time`, per second.

        Zero unless overridden: a generator with no notion of a rate (or one
        that has landed) is not moving. A rate feedforward uses this rather
        than differencing successive `generate()` values, which would carry
        the reading noise a real trajectory does not have.
        """
        return 0.0
