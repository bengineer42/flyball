"""Helpers `Device.__init_subclass__` uses to build a class's tree, commands and setters."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, get_args, get_origin, get_type_hints

from ..quantities.quantity import Quantity
from ..quantities.si import Unitless
from .commands import CommandSpec, Param
from .descriptors import Descriptor, Input, Namespace
from .signal import Access, NodeSpec, Role, SignalSpec

if TYPE_CHECKING:
    from .device import Device


def _inputs(items: Iterable[Namespace | Descriptor[Any]]) -> Iterator[Input]:
    for item in items:
        if isinstance(item, Input):
            yield item
        elif isinstance(item, Namespace):
            yield from _inputs(item.children)


@dataclass(frozen=True, slots=True)
class _Leaf:
    path: str
    role: Role
    spec: SignalSpec


def _leaves(tree: Iterable[NodeSpec | SignalSpec], above: str = "") -> Iterator[_Leaf]:
    for spec in tree:
        path = f"{above}.{spec.name}" if above else spec.name
        if isinstance(spec, SignalSpec):
            yield _Leaf(path, spec.role, spec)
        else:
            yield from _leaves(spec.children, path)


_LINKABLE = frozenset({Role.DEMAND, Role.SETTING})
"""What a command argument may be a value for: a demand (clamped to its limits) or a setting."""


def _link_params(cls: type[Device], fn: Callable[..., Any]) -> dict[str, Param]:
    """Each argument of `fn` with the demand it is for: `Annotated[...]` first, then by name.

    Resolves the annotations against the class body, so `Annotated[Flow,
    dry_flow]` finds the descriptor, and writes the resolved ones back onto the
    function: whatever builds a request model from it later needs no
    class namespace.
    """
    try:
        hints = get_type_hints(fn, include_extras=True, localns=dict(vars(cls)))
    except NameError:
        return {}  # a forward reference; the server's model derives it later
    fn.__annotations__ = hints
    descriptors = cls.DESCRIPTORS
    params: dict[str, Param] = {}
    for name, parameter in list(inspect.signature(fn).parameters.items())[1:]:
        annotation = hints.get(name, Any)
        link: Descriptor[Any] | None = None
        if get_origin(annotation) is Annotated:
            link = next((m for m in get_args(annotation)[1:] if isinstance(m, Descriptor)), None)
        if link is None and (d := descriptors.get(name)) is not None and d.role in _LINKABLE:
            link = d
        if link is not None and name not in hints:
            # No annotation: the demand's type is the argument's, for the request model.
            annotation = hints[name] = Annotated[link.vtype, link]
        params[name] = Param(
            name, annotation, None if link is None else link.path, parameter.default
        )
    return params


def _setter(cls: type[Device], leaf: _Leaf) -> CommandSpec:
    """`set_<path>(value)` for a demand no command sets; the rig routes it to its demand path."""
    tag = "set_" + leaf.path.replace(".", "_")
    label = leaf.spec.label or leaf.spec.name.replace("_", " ")

    def setter(self: Device, value: float) -> None:
        raise NotImplementedError("a synthesised setter runs through the rig's demand path")

    setter.__name__ = tag
    setter.__qualname__ = f"{cls.__qualname__}.{tag}"
    setter.__doc__ = f"Set {label}."
    setter.__annotations__ = {"value": leaf.spec.vtype, "return": None}
    param = Param("value", leaf.spec.vtype, None, inspect.Parameter.empty)
    return CommandSpec(tag, setter, {"value": param}, demand_of=leaf.path)


def _last_of(cls: type[Device]) -> NodeSpec:
    """`last.<tag>`: when each command last ran and with what, for the wire and the record."""
    return NodeSpec(
        name="last",
        label="Last run",
        children=tuple(
            SignalSpec(
                name=tag,
                quantity=Quantity(tag, Unitless),
                access=Access.RP,
                vtype=dict[str, Any],
                label=tag.replace("_", " "),
            )
            for tag, spec in cls.commands.items()
            if not spec.simulation and spec.demand_of is None
        ),
    )
