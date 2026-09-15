"""Sinks, observers and actuators.

An actuator is described by three models, each a frozen dataclass so pydantic
can schema it:

- **config** -- what it was built from. A :class:`~flyball.core.config.Config`,
  so it is also what builds it, and the resolved form is what a schema shows.
- **state** -- what changes tick to tick: the demand, and whatever the device
  adds (flows, efforts, a mode). Every actuator has one; a bare output's is
  just the demand.
- **view** -- config and state together at one instant, for the wire. Nested
  rather than flattened so a client can tell what changes from what never does.

An actuator declares nothing beyond its ``config`` and ``state`` properties:
their return annotations are read on subclassing, checked to be the bases
above, and checked to be describable by pydantic. Methods marked with
:func:`command` are collected the same way, so a server or a program can find
every action an actuator offers without a list being kept by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, get_type_hints, overload

from pydantic import TypeAdapter
from pydantic.errors import (
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    PydanticUndefinedAnnotation,
)

from .config import Config
from .reading import Channel, Reading, Sample, Source
from .units import Unit


class Sink:
    """Takes something in, commits on ``apply``. Base for actuators and buffering observers.

    ``name`` identifies it in the rig, on the wire and in a recording -- for
    an actuator it is also the name of the loop driving it.
    """

    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def apply(self) -> None:
        """Commit whatever was handed over since the last apply. Default: nothing."""


class Observer[S: Sample | Reading]:
    """Hears samples from the sources and channels it names.

    ``observe`` is called once per sample whose source, or any of whose
    channels, is in ``observes`` -- an observer keyed on a channel still gets
    the whole sample and picks its measurand. ``touches`` lists the sinks the
    rig should ``apply`` after a delivery in which this observer fired.
    """

    name: str
    observes: frozenset[Source | Channel]
    touches: frozenset[Sink] = frozenset()

    def observe(self, sample: S) -> None:
        raise NotImplementedError


# Keyword-only so a subclass can add required fields after this base's
# defaulted ``demand`` without dataclass complaining about field order.
@dataclass(frozen=True, slots=True, kw_only=True)
class ActuatorState:
    """What an actuator is doing now. Subclasses add the device's own fields."""

    demand: float | None = None
    """The last demand handed over by the loop, in the loop's measurand unit."""


class ActuatorConfig[A: "Actuator[Any, Any]"](Config[A]):
    """What an actuator is built from, and what builds it."""


@dataclass(frozen=True, slots=True)
class ActuatorView[C: ActuatorConfig[Any], S: ActuatorState]:
    """An actuator at one instant: how it was configured and what it is doing."""

    config: C
    state: S


def _schemable(owner: type, attr: str, model: type, mode: str) -> None:
    """Fail at class definition if pydantic cannot describe ``model``."""
    try:
        TypeAdapter(model).json_schema(mode=mode)  # pyright: ignore[reportArgumentType]
    except PydanticUndefinedAnnotation:
        pass  # a forward reference; resolved on first real use
    except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema) as e:
        raise TypeError(
            f"{owner.__name__}.{attr} ({model.__name__}) has no JSON schema: {e}"
        ) from e


def _declared_return(cls: type, prop: str) -> Any:
    """The return annotation of ``cls``'s own ``prop`` property; None if not overridden here."""
    attr = cls.__dict__.get(prop)
    if not isinstance(attr, property) or attr.fget is None:
        return None
    try:
        return get_type_hints(attr.fget).get("return")
    except NameError:
        return None  # a forward reference; the inherited type stands


#: Path segments the server uses after an actuator's name; no command may take them.
RESERVED_NAMES = frozenset({"schema"})


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One method an actuator exposes as a command, found by :func:`command`."""

    tag: str
    method: Callable[..., Any]

    @property
    def doc(self) -> str | None:
        return self.method.__doc__


@overload
def command[F: Callable[..., Any]](fn: F, /) -> F: ...
@overload
def command[F: Callable[..., Any]](*, tag: str) -> Callable[[F], F]: ...
def command(fn: Any = None, /, *, tag: str | None = None) -> Any:
    """Mark an actuator method as a command, under its name or ``tag``.

    ``@command`` or ``@command(tag="stop")``. The method's signature is the
    command's: the server derives the request from it, a program the arguments.
    """

    def mark(f: Any) -> Any:
        f.__command__ = tag or f.__name__
        return f

    return mark(fn) if fn is not None else mark


class Actuator[C: ActuatorConfig[Any], S: ActuatorState](Sink):
    """Something a loop drives.

    ``config_type`` and ``state_type`` are read off the ``config`` and ``state``
    properties on subclassing, so a subclass writes each type once, in the
    annotation. ``commands`` collects every method marked :func:`command`, the
    parent's included. ``demand_unit`` is what :meth:`set_demand` is in -- the
    loop's measurand, or None for a unitless demand.
    """

    config_type: ClassVar[type[ActuatorConfig[Any]]] = ActuatorConfig
    state_type: ClassVar[type[ActuatorState]] = ActuatorState
    demand_unit: ClassVar[Unit | None] = None
    commands: ClassVar[dict[str, CommandSpec]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for prop, attr, base, mode in (
            ("config", "config_type", ActuatorConfig, "validation"),  # arrives over the wire
            ("state", "state_type", ActuatorState, "serialization"),  # only ever leaves
        ):
            model = _declared_return(cls, prop)
            if model is None:
                continue
            if not (isinstance(model, type) and issubclass(model, base)):
                raise TypeError(
                    f"{cls.__name__}.{prop} must return a {base.__name__}, not {model!r}"
                )
            setattr(cls, attr, model)
            _schemable(cls, attr, model, mode)

        # Own dict, extended from the parent's: a subclass adds commands, and
        # marking one in a subclass must not leak into its siblings.
        cls.commands = dict(cls.commands)
        for attr_name, value in cls.__dict__.items():
            if (tag := getattr(value, "__command__", None)) is not None:
                if tag in RESERVED_NAMES:
                    raise ValueError(f"{cls.__name__}: {tag!r} is reserved as a route segment")
                if tag in cls.commands and cls.commands[tag].method.__name__ != attr_name:
                    raise ValueError(f"{cls.__name__}: command tag {tag!r} is already used")
                cls.commands[tag] = CommandSpec(tag, value)

    def set_demand(self, demand: float) -> float | None:
        """Take a demand; return the value the loop should expect, if it differs."""
        raise NotImplementedError

    @property
    def config(self) -> C:
        raise NotImplementedError

    @property
    def state(self) -> S:
        raise NotImplementedError

    @property
    def view(self) -> ActuatorView[C, S]:
        return ActuatorView(self.config, self.state)
