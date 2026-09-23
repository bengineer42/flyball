"""`SetPointGenerator`: the base a trajectory subclasses, and the schema it generates by doing so.

`control/setpoint.py` holds the concrete generators that ship (`Dwell`,
`LinearRampSetpoint`, `Profile`, ...) and the closed discriminated union over
them (`GeneratorConfig`); this is just the machinery every one subclasses,
the same shape [ControlLaw][flyball.model.law.ControlLaw] gives laws.
"""

from __future__ import annotations

from inspect import signature
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_snake
from pydantic_core import core_schema

from flyball.model.model import ModelOf, creation_model


class SetPointGeneratorConfig(BaseModel):
    """How a generator was specified: its constructor arguments and its tag.

    `tag` is declared on the base so the base has a schema; each subclass
    narrows it to a `Literal`, which lets a union of configs discriminate on
    it -- the same shape [ControlLawConfig][flyball.model.law.ControlLawConfig]
    gives laws.
    """

    # A NaN or infinite end, value or rate would carry straight into the setpoint.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    generator: ClassVar[type[SetPointGenerator]]
    init_names: ClassVar[tuple[str, ...]] = ()

    tag: str

    def build(self) -> SetPointGenerator:
        """A fresh generator, not yet started."""
        return self.generator(**{name: getattr(self, name) for name in self.init_names})


class SetPointGenerator:
    """A reference trajectory. Subclassing derives `config`; registering is explicit.

    A tag is assigned when subclassed (`class Dwell(SetPointGenerator, tag="dwell")`),
    but nothing is written into a shared registry any more -- see
    [Catalogs][flyball.model.catalog.Catalogs].
    """

    tag: ClassVar[str] = ""
    config: ClassVar[Any] = None
    view_fields: ClassVar[tuple[str, ...]] = ()
    """Attributes beyond the constructor's that a running instance shows on the wire."""
    end_time: float | None = None
    """When the trajectory lands, in the time `start` was given; None until started, or endless."""

    def __init_subclass__(cls, tag: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or to_snake(cls.__name__)

        # Every generator gets its own config, derived from `__init__`: the
        # field list a form needs to build one, plus the tag that names it.
        if "config" not in cls.__dict__:
            config_model = creation_model(
                cls,
                suffix="Config",
                base=SetPointGeneratorConfig,
                extra={"tag": (Literal[cls.tag], cls.tag)},
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
        """`{"tag": ..., **the constructor's arguments, **view_fields present so far}`."""
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

    def generate(self, time: float) -> float:
        """The set point at `time`."""
        raise NotImplementedError

    def finished(self, time: float) -> bool:
        """Whether the trajectory has landed by `time` and nothing further will change.

        In the controller's time, as `generate` and `rate` take it, so a
        scaled or stepped clock is judged where it stands rather than by a
        wall-clock timer per trajectory.
        """
        return False

    def rate(self, time: float) -> float:
        """How fast the set point is moving at `time`, per second.

        Zero unless overridden: a generator with no notion of a rate (or one
        that has landed) is not moving. A rate feedforward uses this rather
        than differencing successive `generate()` values, which would carry
        the reading noise a real trajectory does not have.
        """
        return 0.0
