"""`ControlLaw`: the base a law subclasses, and the schema it generates by doing so.

`control/laws.py` holds the concrete laws that ship (`PI`, `PID`, ...); this
is just the machinery every one of them subclasses -- `__init_subclass__`,
`creation_model()` wiring -- the Config/Instance tiers of the Catalog model
for one kind of component (laws). A `Tuning` (a saved, named `ControlLawConfig`
or `ControlLawView`) lives in `flyball.library.tunings`, not here: `library`
sits above `model` in the layer ordering, so nothing in this module imports
it -- see `ControlLawBuilder`/`ControlLawLike` below, which accept a `Tuning`
structurally (anything with a `build()`) rather than by name.
"""

from __future__ import annotations

from inspect import signature
from typing import Any, ClassVar, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, create_model

from flyball.foundation import Labelled
from flyball.model.model import ModelOf, creation_model


class Transfer(Labelled):
    NONE = "none", "Do not transfer any correction"
    CARRY = "carry", "Keep the correction the law is already holding"
    TRACK = "track", "Seed the correction from what the pumps are delivering"
    RESET = "reset", "Start the law cold"


class ControlLawConfig(BaseModel):
    """How a law was specified: its constructor arguments and its tag.

    `tag` is declared on the base so the base has a schema; each subclass
    narrows it to a `Literal`, which lets a union of configs discriminate on
    it.
    """

    model_config = ConfigDict(extra="forbid")

    law: ClassVar[type]
    init_names: ClassVar[tuple[str, ...]] = ()

    tag: str

    def build(self) -> Any:
        """A fresh law with cold state.

        [ControlLawView][flyball.model.law.ControlLawView] resumes one.
        """
        return self.law(**{name: getattr(self, name) for name in self.init_names})


class ControlLawState(BaseModel):
    """What a law is doing right now: the values it carries between steps."""

    state_names: ClassVar[tuple[str, ...]] = ()

    def apply(self, law: Any) -> Any:
        """Write this state onto `law` in place and return it."""
        for name in self.state_names:
            setattr(law, name, getattr(self, name))
        return law


class ControlLawView(ControlLawConfig, ControlLawState):
    """Config and state together: everything needed to reproduce a running law."""

    @classmethod
    def of(cls, config: ControlLawConfig, state: ControlLawState) -> Self:
        """`config` and `state` flattened into one view.

        Raises:
            TypeError: If `config` describes a different law from this view.
        """
        view = cls if cls is not ControlLawView else config.law.view
        if config.law is not view.law:
            raise TypeError(
                f"{type(config).__name__} configures {config.law.__name__}, not {view.law.__name__}"
            )
        return view(**{**config.model_dump(), **state.model_dump()})

    def build(self) -> Any:
        """The law, rebuilt with this view's arguments and state applied."""
        return self.apply(super().build())


def _model_fields(model: type[BaseModel]) -> dict[str, Any]:
    """A model's fields back in `create_model` form, to merge into another."""
    return {
        name: (field.annotation, ... if field.is_required() else field.default)
        for name, field in model.model_fields.items()
    }


def _resolved_model(cls: type, name: str) -> type[BaseModel] | None:
    """The model behind `cls.name`, own or inherited, if it is a `ModelOf`."""
    for klass in cls.__mro__:
        attr = klass.__dict__.get(name)
        if isinstance(attr, ModelOf):
            return attr.model
        if attr is not None:
            return None
    return None


def _merged_state(cls: type) -> dict[str, Any]:
    """Every `state_fields` up the MRO, base first so subclasses extend."""
    merged: dict[str, Any] = {}
    for klass in reversed(cls.__mro__):
        merged.update(klass.__dict__.get("_state_fields", {}))
    return merged


class ControlLaw:
    """Base for control laws. Subclassing generates the law's pydantic models.

    - `config`: one field per `__init__` parameter, plus `tag`; builds the law
      with [ControlLawConfig.build][flyball.model.law.ControlLawConfig.build].
    - `state`: one field per name in `_state_fields`, merged up the MRO.
    - `view`: both flattened, round-tripping through
      [ControlLawView.build][flyball.model.law.ControlLawView.build].

    Each is a [ModelOf][flyball.model.model.ModelOf]: `Law.config` is the model
    class, `law.config` that law's values. A law that declares one itself keeps
    it. The wire name is the class keyword `tag`
    (`class PI(ControlLaw, tag="PI")`), defaulting to the class name.
    """

    tag: ClassVar[str] = None  # pyright: ignore[reportAssignmentType]
    config: ClassVar[Any] = None
    state: ClassVar[Any] = None
    view: ClassVar[Any] = None
    _state_fields: ClassVar[dict[str, Any]] = {}

    def __init_subclass__(cls, tag: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.tag = tag or cls.__dict__.get("tag") or cls.__name__

        # Every law gets its own config and state, even one that adds neither:
        # the models carry `law`, so an inherited pair would rebuild the base.
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
        # law answers `state` and `view` the same way.
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

    def reset(self) -> None:
        return None

    def resume(self, reading: float, setpoint: float, correction: float) -> float:
        """Re-enter control so the first step reproduces `correction`; return what was seeded.

        For bumpless hand-back from manual control. The seeded value differs
        from `correction` when the law has no integral to carry an offset; the
        difference is the bump. Default is a cold start; a law that can compute
        its first correction should override.
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


@runtime_checkable
class ControlLawBuildable(Protocol):
    """Anything that builds a `ControlLaw` -- a config, a view, or a `Tuning`.

    Structural, not by name: a `Tuning` (`flyball.library.tunings`, above this
    layer) satisfies this without `model/law.py` importing it.
    """

    def build(self) -> Any: ...


type ControlLawBuilder = ControlLawConfig | ControlLawView
"""The two pydantic-model shapes a law can be specified by -- what a `Tuning.config` holds."""

type ControlLawLike = ControlLaw | ControlLawBuildable
"""Anything settable as a controller's law: a live law, a config, a view, or a `Tuning`."""
