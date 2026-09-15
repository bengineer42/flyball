from __future__ import annotations

from dataclasses import dataclass
from inspect import signature
from typing import Any, ClassVar, Literal, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, SerializeAsAny, create_model

from flyball.core import Labelled
from flyball.core.model import ModelOf, creation_model


class Transfer(Labelled):
    NONE = "none", "Do not transfer any correction"
    CARRY = "carry", "Keep the correction the law is already holding"
    TRACK = "track", "Seed the correction from what the pumps are delivering"
    RESET = "reset", "Start the law cold"


class ControlLawConfig(BaseModel):
    """How a law was specified: its constructor arguments and its tag.

    ``tag`` is declared here rather than only on the generated subclasses so the
    declared type carries it too: a value annotated as this class has a readable
    tag, and the base publishes a real schema. Each subclass narrows it to a
    ``Literal``, which is what lets a union of configs discriminate on it.
    """

    model_config = ConfigDict(extra="forbid")

    law: ClassVar[type]
    init_names: ClassVar[tuple[str, ...]] = ()

    tag: str

    def build(self) -> Any:
        """The law this config describes, constructed from its fields.

        Returns:
            A fresh law. Its state starts cold; use :class:`ControlLawView` to
            resume one where it left off.
        """
        return self.law(**{name: getattr(self, name) for name in self.init_names})

    def to_tuning(self, tag) -> Tuning:
        return Tuning(tag=tag, config=self)


class ControlLawState(BaseModel):
    """What a law is doing right now: the values it carries between steps."""

    state_names: ClassVar[tuple[str, ...]] = ()

    def apply(self, law: Any) -> Any:
        """Write this state back onto a running law.

        Args:
            law: The law to write onto, modified in place.

        Returns:
            The same law.
        """
        for name in self.state_names:
            setattr(law, name, getattr(self, name))
        return law


class ControlLawView(ControlLawConfig, ControlLawState):
    """Config and state together: everything needed to reproduce a running law."""

    @classmethod
    def of(cls, config: ControlLawConfig, state: ControlLawState) -> Self:
        """The view for a law described by ``config`` and running at ``state``.

        Args:
            config: How the law was specified.
            state: What it is doing.

        Returns:
            The two flattened into one view.

        Raises:
            TypeError: If ``config`` describes a different law from this view.
        """
        view = cls if cls is not ControlLawView else config.law.view
        if config.law is not view.law:
            raise TypeError(
                f"{type(config).__name__} configures {config.law.__name__}, not {view.law.__name__}"
            )
        return view(**{**config.model_dump(), **state.model_dump()})

    def build(self) -> Any:
        """The law, rebuilt and resumed where the view left it.

        Returns:
            A law with this view's arguments and its state already applied.
        """
        return self.apply(super().build())


def _model_fields(model: type[BaseModel]) -> dict[str, Any]:
    """A model's fields back in ``create_model`` form, to merge into another."""
    return {
        name: (field.annotation, ... if field.is_required() else field.default)
        for name, field in model.model_fields.items()
    }


def _resolved_model(cls: type, name: str) -> type[BaseModel] | None:
    """The model behind ``cls.name``, own or inherited, if it is a ``ModelOf``."""
    for klass in cls.__mro__:
        attr = klass.__dict__.get(name)
        if isinstance(attr, ModelOf):
            return attr.model
        if attr is not None:
            return None
    return None


def _merged_state(cls: type) -> dict[str, Any]:
    """Every ``state_fields`` up the MRO, base first so subclasses extend."""
    merged: dict[str, Any] = {}
    for klass in reversed(cls.__mro__):
        merged.update(klass.__dict__.get("_state_fields", {}))
    return merged


ControlLaws: dict[str, type[ControlLaw]] = {}


class ControlLaw:
    """Base for control laws, giving each subclass its own models.

    Subclassing generates three pydantic models from the law itself, so a law is
    described once and never again by hand:

    - ``config``: one field per ``__init__`` parameter, plus ``tag``. Builds the
      law with :meth:`ControlLawConfig.build`.
    - ``state``: one field per name in ``_state_fields``, merged up the MRO so a
      subclass extends its bases rather than replacing them.
    - ``view``: both flattened into one model, round-tripping through
      :meth:`ControlLawView.build`.

    Each is a :class:`~flyball.utils.ModelOf`, so ``Law.config`` is the model
    class and ``law.config`` is that law's values. A law that declares any of the
    three itself keeps its own.

    Args:
        tag: The wire name for this law, defaulting to the class name. Passed as
            a class keyword: ``class PI(ControlLaw, tag="PI")``.
    """

    tag: ClassVar[str] = None  # pyright: ignore[reportAssignmentType]
    config: ClassVar[Any] = None
    state: ClassVar[Any] = None
    view: ClassVar[Any] = None
    _state_fields: ClassVar[dict[str, Any]] = {}

    def __init_subclass__(
        cls, tag: str | None = None, register: bool = True, **kwargs: Any
    ) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or cls.__name__

        # Every law gets its own config and state, even one that adds neither:
        # the models carry ``law``, so an inherited pair would rebuild the base.
        # The config carries the tag: nothing crosses the wire usefully without
        # saying which law it configures.
        if "config" not in cls.__dict__:
            config_model = creation_model(
                cls,
                suffix="Config",
                base=ControlLawConfig,
                extra={"tag": (Literal[cls.tag], cls.tag)},
            )
            config_model.law = cls  # pyright: ignore[reportAttributeAccessIssue]
            config_model.init_names = tuple(signature(cls).parameters)  # pyright: ignore[reportAttributeAccessIssue]
            cls.config = ModelOf(config_model, tuple(config_model.model_fields))

        # A stateless law gets an empty state model rather than none, so every
        # law answers ``state`` and ``view`` the same way.
        if "state" not in cls.__dict__:
            state_fields = _merged_state(cls)
            state_model = create_model(  # pyright: ignore[reportCallIssue]
                cls.__name__ + "State",
                __base__=ControlLawState,
                **{n: (t, ...) for n, t in state_fields.items()},  # pyright: ignore[reportArgumentType]
            )
            state_model.state_names = tuple(state_fields)  # pyright: ignore[reportAttributeAccessIssue]
            cls.state = ModelOf(state_model, tuple(state_fields))

        # A view is both halves flattened into one model: how the law was
        # configured and what it is doing right now. The tag arrives via the
        # config. Resolved rather than reused, so a law that declares its own
        # config or state is still viewable.
        config_model = _resolved_model(cls, "config")
        state_model = _resolved_model(cls, "state")
        if "view" not in cls.__dict__ and config_model is not None and state_model is not None:
            view_model = create_model(  # pyright: ignore[reportCallIssue]
                cls.__name__ + "View",
                __base__=ControlLawView,
                **_model_fields(config_model),  # pyright: ignore[reportArgumentType]
                **_model_fields(state_model),  # pyright: ignore[reportArgumentType]
            )
            view_model.law = cls
            view_model.init_names = config_model.init_names  # pyright: ignore[reportAttributeAccessIssue]
            view_model.state_names = state_model.state_names  # pyright: ignore[reportAttributeAccessIssue]
            cls.view = ModelOf(view_model, tuple(view_model.model_fields))

        if register:
            if cls.tag in ControlLaws:
                raise ValueError(f"Law with tag '{cls.tag}' is already registered.")
            ControlLaws[cls.tag] = cls

    def reset(self) -> None:
        return None

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        """Re-enter control so the first step reproduces ``correction``.

        Used to hand back from manual pump control without stepping the output.
        Returns the correction actually seeded, which is not ``correction`` when
        the law has no integral to carry an offset: a proportional law cannot be
        bumpless because it has no memory. Compare the two to size the bump the
        hand-back will put through the pumps.

        The default is a cold start carrying no offset. A law that can compute
        the correction it will actually produce should override this.
        """
        return correction

    def step(
        self,
        elapsed: float,
        reading: float,
        setpoint: float,
        last_applied: float | None = None,
    ) -> float:
        return 0.0


@dataclass(slots=True, frozen=True)
class ControllerState:
    setpoint: float | None
    correction: float
    last_value: float | None
    law: ControlLawState | Exception


@dataclass(slots=True, frozen=True)
class ControllerView(ControllerState):
    law: ControlLawView | Exception

    # @classmethod
    # def of(cls, config: ControlLawConfig, state: ControllerState) -> Self:
    #     return cls(
    #         generator=state.generator,
    #         setpoint=state.setpoint,
    #         correction=state.correction,
    #         last_value=state.last_value,
    #         law=(
    #             ControlLawView.of(config, state.law)
    #             if config.law is not None and state.law is not None
    #             else None
    #         ),
    #     )


type ControlLawLike = ControlLaw | ControlLawConfig | ControlLawView | Tuning

type ControlLawBuilder = ControlLawConfig | ControlLawView


@dataclass(slots=True, frozen=True)
class Tuning:
    tag: str
    # Serialised by its runtime type: declared as the base, a response would
    # carry only ``tag`` and drop every gain the law actually has.
    config: SerializeAsAny[ControlLawBuilder]

    def build(self) -> ControlLaw:
        return self.config.build()

    @property
    def tuple(self) -> tuple[str, SerializeAsAny[ControlLawBuilder]]:
        return (self.tag, self.config)


class ValueSource(Labelled):
    """Where a ramp begins."""

    PROCESS = "process", "The current reading"
    SETPOINT = "setpoint", "The current target"
    DEMAND = "demand", "The current demand"


class ApplyResult(NamedTuple):
    demand: float
    expected: float | None
    delivered_correction: float | None


class RegulateResult(NamedTuple):
    demand: float
    expected: float | None
    delivered_correction: float | None
    bump: float


class Tunings:
    def __init__(self, tunings: list[Tuning] | None = None) -> None:
        self._tunings: dict[str, ControlLawBuilder] = {}
        if tunings is not None:
            for tuning in tunings:
                if tuning.tag in self._tunings:
                    raise ValueError(f"duplicate tuning tag {tuning.tag!r}")
                self._tunings[tuning.tag] = tuning.config

    def add(self, tuning: Tuning) -> None:
        self._tunings[tuning.tag] = tuning.config

    def get(self, tag: str) -> SerializeAsAny[ControlLawBuilder] | None:
        return self._tunings.get(tag)

    def all(self) -> dict[str, ControlLawConfig | ControlLawView]:
        return dict(self._tunings)

    def list(self) -> list[Tuning]:
        return [Tuning(tag=tag, config=config) for tag, config in self._tunings.items()]
