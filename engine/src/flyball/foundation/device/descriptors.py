"""Descriptors: what a device's structure is declared with, in a class body or from config.

`flows = Namespace("flows", "Flows")` on the class, `dry_flow = flows.demand(...)`; on the
class a descriptor is its spec, on an instance it is the bound
[Signal][flyball.foundation.device.signal.Signal].
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self, overload

from ..errors import NotReadyError
from ..quantities.quantity import Quantity
from ..quantities.si import Unitless
from .signal import (
    Access,
    Bound,
    Node,
    NodeSpec,
    Role,
    Section,
    Signal,
    SignalRef,
    SignalSpec,
    Value,
)

if TYPE_CHECKING:
    from .device import Device


class Namespace:
    """A namespace declared in a class body, or built from config: a builder for a NodeSpec.

    `flows = Namespace("flows", "Flows")` on the class; `self.flows` is the
    bound [Node][flyball.foundation.device.signal.Node]. Its `demand`, `output`, `config`
    and `input` make the signals under it.
    """

    def __init__(
        self,
        name: str,
        label: str = "",
        *,
        atomic: bool = False,
        poll_s: float | None = None,
        parent: Namespace | None = None,
    ) -> None:
        # Instance attributes only: a class-level annotation of a type that
        # has `__get__` reads as a descriptor to a checker.
        self.name = name
        self.label = label
        self.atomic = atomic
        self.poll_s = poll_s
        self.parent = parent
        self.children: list[Namespace | Descriptor[Any]] = []
        self.attr: str | None = None
        if parent is not None:
            parent.children.append(self)

    @property
    def path(self) -> str:
        """The dotted path relative to the device: `"flows"`, `"bank_a.ch1"`."""
        return self.name if self.parent is None else f"{self.parent.path}.{self.name}"

    def namespace(self, name: str, label: str = "", **options: Any) -> Namespace:
        """A namespace under this one; `atomic` and `poll_s` as for the top level."""
        return Namespace(name, label, parent=self, **options)

    def demand(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Demand:
        """A settable signal under this namespace, with a readback; what a controller drives."""
        return Demand(name, label, *args, parent=self, **meta)

    def output(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Output:
        """A produced signal under this namespace: a measurement, a derived value, a mode."""
        return Output(name, label, *args, parent=self, **meta)

    def setting(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Setting:
        """A signal under this namespace that a command re-sets; shown, not driven."""
        return Setting(name, label, *args, parent=self, **meta)

    def config(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> ConfigSignal:
        """A signal under this namespace effective at build: the driver pushes it once."""
        return ConfigSignal(name, label, *args, parent=self, **meta)

    def input(self, name: str | Section, label: str = "", *args: Any, **meta: Any) -> Input:
        """An input grouped under this namespace for the schema; not in the tree (rig-bound)."""
        return Input(name, label, *args, parent=self, **meta)

    def spec(self) -> NodeSpec | None:
        """The namespace as a spec; None for one holding only inputs (they are not in the tree)."""
        children = [c.spec() for c in self.children if not isinstance(c, Input)]
        if self.children and not children:
            return None
        return NodeSpec(
            name=self.name,
            label=self.label,
            atomic=self.atomic,
            poll_s=self.poll_s,
            children=tuple(c for c in children if c is not None),
        )

    def __set_name__(self, owner: type, attr: str) -> None:
        self.attr = attr
        if self.parent is None:
            _declare(owner, self)

    @overload
    def __get__(self, instance: None, owner: type) -> Namespace: ...
    @overload
    def __get__(self, instance: Device, owner: type) -> Node: ...
    def __get__(self, instance: Device | None, owner: type) -> Namespace | Node:
        if instance is None:
            return self
        return instance.nodes[self.path]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.path!r})"


class Descriptor[B]:
    """A signal declared in a class body, or built from config: a builder for a SignalSpec.

    `dry_flow = flows.demand("dry", "Dry pump flow", FLOW, limits=(0.0, dry_max_flow))`
    on the class; `self.dry_flow` is the bound [Signal][flyball.foundation.device.signal.Signal].
    A [Section][flyball.foundation.device.signal.Section] in place of the name gives the
    segment and tags the signal. `quantity` None: the name, unitless (a mode, a
    count). A limit may be another descriptor of the same device: its
    current value bounds this one. `ceiling` lets the rig file widen `access`
    up to it (never beyond); without one, the rig file may only narrow.
    """

    role: ClassVar[Role]

    def __init__(
        self,
        name: str | Section,
        label: str = "",
        quantity: Quantity | None = None,
        vtype: Any = float,
        *,
        access: Access | None = None,
        ceiling: Access | None = None,
        parent: Namespace | None = None,
        **meta: Any,
    ) -> None:
        self.section: Section | None
        if isinstance(name, Section):
            self.section, self.name = name, name.name
            if not label:
                label = name.label
        else:
            self.section, self.name = meta.pop("section", None), name
        self.label = label
        self.quantity = Quantity(self.name, Unitless) if quantity is None else quantity
        self.vtype = vtype
        self.access = self.role.access if access is None else Access.check(access)
        self.ceiling = None if ceiling is None else Access.check(ceiling)
        self.meta = meta
        self.parent = parent
        self.attr: str | None = None
        if parent is not None:
            parent.children.append(self)

    @property
    def path(self) -> str:
        """The dotted path relative to the device: `"humidity"`, `"flows.dry"`."""
        return self.name if self.parent is None else f"{self.parent.path}.{self.name}"

    def spec(self) -> SignalSpec:
        """The signal as a spec, a descriptor limit resolved to a reference by path."""
        meta = dict(self.meta)
        if (limits := meta.get("limits")) is not None:
            meta["limits"] = tuple(_bound(b) for b in limits)
        return SignalSpec(
            name=self.name,
            quantity=self.quantity,
            access=self.access,
            ceiling=self.ceiling,
            role=self.role,
            section=self.section,
            vtype=self.vtype,
            label=self.label,
            **meta,
        )

    def __set_name__(self, owner: type, attr: str) -> None:
        self.attr = attr
        if self.parent is None:
            _declare(owner, self)

    @overload
    def __get__(self, instance: None, owner: type) -> Self: ...
    @overload
    def __get__(self, instance: Device, owner: type) -> B: ...
    def __get__(self, instance: Device | None, owner: type) -> Self | B:
        if instance is None:
            return self
        return self.on(instance)

    def on(self, device: Device) -> B:
        """What this descriptor is on an instance: the bound signal."""
        return device.signals[self.path]  # type: ignore[return-value]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.path!r})"


def _bound(bound: Any) -> Bound:
    """A limit as the spec carries it: a number, or a reference to a descriptor's signal.

    An input is referenced by its role, wherever it was grouped for the schema.
    """
    if isinstance(bound, Input):
        return SignalRef(bound.name)
    if isinstance(bound, Descriptor):
        return SignalRef(bound.path)
    if isinstance(bound, SignalRef):
        return bound
    return float(bound)


class Demand(Descriptor[Signal]):
    """Settable, with a current value that updates; what a controller drives. `RPW`."""

    role = Role.DEMAND


class Output(Descriptor[Signal]):
    """Produced, never set: a measurement, a derived value, a mode. `RP`."""

    role = Role.OUTPUT


class Setting(Descriptor[Signal]):
    """Re-set by a command while the device runs; shown, not driven. `RP`."""

    role = Role.SETTING


class ConfigSignal(Descriptor[Signal]):
    """Effective at build, shown, never set at run time: the driver pushes it once. `R`."""

    role = Role.CONFIG


class BoundInput:
    """An input on an instance: the source signal the rig bound, and its current value."""

    __slots__ = ("device", "input")

    def __init__(self, device: Device, input: Input) -> None:
        self.device = device
        self.input: Any = input  # not a class-level Descriptor annotation: see Namespace

    @property
    def signal(self) -> Signal | None:
        """The bound source, or None until the rig binds one."""
        bound = self.device.bound.get(self.input.name)
        return bound if isinstance(bound, Signal) else None

    @property
    def value(self) -> Value:
        """The source's newest value; the input's default before one arrives.

        Raises:
            NotReadyError: Nothing bound or read, and no default.
        """
        if (signal := self.signal) is not None and (reading := signal.reading) is not None:
            return reading.value
        default = self.input.default
        if isinstance(default, Descriptor):
            return self.device.signals[default.path].value
        if default is None:
            raise NotReadyError(f"{self.device.name}.{self.input.name}: nothing has been read")
        return default


class Input(Descriptor[BoundInput]):
    """Another device's signal, bound by the rig to this role (`bound: {dry: ...}`).

    Not in the device's tree: `self.dry_supply` is the source signal once
    bound, and reads `default` (a number, or a config descriptor) before
    that or when nothing has been read on it yet.
    """

    role = Role.INPUT

    def __init__(
        self,
        name: str | Section,
        label: str = "",
        quantity: Quantity | None = None,
        vtype: Any = float,
        *,
        default: Any = None,
        parent: Namespace | None = None,
        **meta: Any,
    ) -> None:
        super().__init__(name, label, quantity, vtype, parent=parent, **meta)
        self.default = default

    def on(self, device: Device) -> BoundInput:
        """What this input is on an instance: the bound source and its current value."""
        return BoundInput(device, self)


def _declare(owner: type, item: Namespace | Descriptor[Any]) -> None:
    """Note a top-level descriptor on its class, in declaration order, for `__init_subclass__`."""
    declared = owner.__dict__.get("_declared")
    if declared is None:
        declared = []
        owner._declared = declared  # type: ignore[attr-defined]
    declared.append(item)


def _descriptors(cls: type) -> dict[str, Descriptor[Any]]:
    """Every signal descriptor reachable from `cls` and its bases, by attribute name."""
    found: dict[str, Descriptor[Any]] = {}
    for base in reversed(cls.__mro__):
        for item in base.__dict__.get("_declared", ()):
            _collect(item, found)
    return found


def _collect(item: Namespace | Descriptor[Any], found: dict[str, Descriptor[Any]]) -> None:
    if isinstance(item, Descriptor):
        if item.attr is not None:
            found[item.attr] = item
    else:
        for child in item.children:
            _collect(child, found)
