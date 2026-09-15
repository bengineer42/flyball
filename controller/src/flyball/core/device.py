"""Devices: the things a rig is made of, and how they describe themselves.

A device -- a reader or an actuator -- is described by three tiers, told
apart by who changes them:

- **config** -- what it was built from; changed only by rebuilding. A
  :class:`~flyball.core.config.Config`, so it is also what builds it.
- **settings** -- what an operator or a program can re-set while it runs, by
  a command: a blend policy, a sample period, a heater mode.
- **state** -- what the device reports now, every tick or read: the demand,
  the last reading, and any :class:`Condition` that is currently true.

A **view** joins them at one instant for the wire, nested rather than
flattened so a client can tell what changes from what does not.

A device declares nothing beyond its ``config``, ``settings`` and ``state``
properties: their return annotations are read on subclassing, checked to be
the bases above, and checked to be describable by pydantic. Methods marked
with :func:`command` are collected the same way, so a server or a program can
find every action a device offers without a list being kept by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, ClassVar, get_type_hints, overload

from pydantic import TypeAdapter
from pydantic.errors import (
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    PydanticUndefinedAnnotation,
)

from .config import Config


class Level(IntEnum):
    """How much a condition or an event matters. ``logging``'s numbers, so they interleave."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


@dataclass(frozen=True, slots=True)
class Condition:
    """Something true of a device now: offline, railed, overdriven, waiting.

    Appears in the device's state while it holds and goes when it clears; a
    late-joining client sees the present, not a log of the past.
    """

    kind: str  #: stable and machine-readable: "offline", "railed"
    level: Level
    message: str
    since_ns: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceState:
    """What a device reports now. Subclasses add their fields."""

    conditions: tuple[Condition, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceSettings:
    """What can be re-set while a device runs. A bare device has nothing."""


class DeviceConfig[D: "Device"](Config[D]):
    """What a device is built from, and what builds it."""

    def build(self) -> D:
        raise NotImplementedError(f"{type(self).__name__} cannot build a device")


@dataclass(frozen=True, slots=True)
class DeviceView[C: DeviceConfig[Any], T: DeviceSettings, S: DeviceState]:
    """A device at one instant: how it was built, how it is set, and what it reports."""

    config: C
    settings: T
    state: S


def _schemable(owner: type, attr: str, model: Any, mode: str) -> None:
    """Fail at class definition if pydantic cannot describe ``model``."""
    try:
        TypeAdapter(model).json_schema(mode=mode)
    except PydanticUndefinedAnnotation:
        pass  # a forward reference; resolved on first real use
    except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema) as e:
        shown = getattr(model, "__name__", repr(model))
        raise TypeError(f"{owner.__name__}.{attr} ({shown}) has no JSON schema: {e}") from e


def _check_command_signature(owner: type, spec: CommandSpec) -> None:
    """Every argument and the return of a command must cross the wire.

    Checked here, not on the first request, so a ``*Like`` alias that admits
    an in-process class fails when the actuator is defined.
    """
    try:
        hints = get_type_hints(spec.method)
    except NameError:
        return  # a forward reference; the server's model derives it later
    for name, annotation in hints.items():
        where = (
            f"{spec.method.__name__}() -> "
            if name == "return"
            else f"{spec.method.__name__}({name})"
        )
        if annotation is type(None):
            continue
        _schemable(owner, where, annotation, "serialization" if name == "return" else "validation")


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


class Device:
    """Base for readers and actuators.

    ``config_type``, ``settings_type`` and ``state_type`` are read off the
    ``config``, ``settings`` and ``state`` properties on subclassing, so a
    subclass writes each type once, in the annotation. ``commands`` collects
    every method marked :func:`command`, the parent's included. A device that
    declares none of the three still answers ``view``: empty config, empty
    settings, a state with no conditions.
    """

    name: str
    config_type: ClassVar[type[DeviceConfig[Any]]] = DeviceConfig
    settings_type: ClassVar[type[DeviceSettings]] = DeviceSettings
    state_type: ClassVar[type[DeviceState]] = DeviceState
    commands: ClassVar[dict[str, CommandSpec]] = {}

    def __init__(self, name: str) -> None:
        self.name = name

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for prop, attr, base, mode in (
            ("config", "config_type", DeviceConfig, "validation"),  # arrives over the wire
            ("settings", "settings_type", DeviceSettings, "validation"),  # set by commands
            ("state", "state_type", DeviceState, "serialization"),  # only ever leaves
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
                spec = CommandSpec(tag, value)
                _check_command_signature(cls, spec)
                cls.commands[tag] = spec

    # A subclass narrows these through its own return annotations -- that is
    # both what the checker sees and what ``__init_subclass__`` reads.
    @property
    def config(self) -> DeviceConfig[Any]:
        """Default: nothing to say. Override with the config the device was built from."""
        return DeviceConfig()

    @property
    def settings(self) -> DeviceSettings:
        """Default: nothing to set. Override alongside the commands that set things."""
        return DeviceSettings()

    @property
    def state(self) -> DeviceState:
        """Default: nothing to report. Override with what the device knows now."""
        return DeviceState()

    @property
    def view(self) -> DeviceView[Any, Any, Any]:
        return DeviceView(self.config, self.settings, self.state)
