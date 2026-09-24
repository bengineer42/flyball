"""The key grammar: what a name a machine matches on may be (D-077, D-079).

A key is lower-case ASCII snake_case, starting with a letter, at most 64 characters:
`^[a-z][a-z0-9_]{0,63}$`. Every key that enters flyball -- a device, link, namespace,
signal or controller segment, a tag's axis and value, a `values:` entry, a rig, board,
program, tuning or dashboard name -- goes through
[check_key][flyball.foundation.keys.check_key], which refuses anything else with a
message naming it.

`-` and `_` are the same in a name (D-079): input may use either, and the canonical
form, the one stored and compared, has `_`. So `name: wet-pump` is found by
`device: wet_pump`, and two names that differ only by `-`/`_` are one name.
[canonical][flyball.foundation.keys.canonical] is that mapping alone, for a lookup,
where a name that is not a key simply finds nothing.

A key is what a machine matches on; what a person reads is a *label*. Every label may be
left blank, and a blank one is [humanise][flyball.foundation.keys.humanise]d from the key
(D-086): `dry_pump_flow` shows as "Dry pump flow".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

KEY: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
"""The canonical grammar, matched whole."""

GRAMMAR: Final = "lower-case letters, digits and `_` (or `-`), starting with a letter, at most 64"


def canonical(name: str) -> str:
    """`name` with every `-` as `_`: the form a key is stored and compared in."""
    return name.replace("-", "_")


def is_key(name: object) -> bool:
    """Whether `name` is a key, in either spelling."""
    return isinstance(name, str) and KEY.fullmatch(canonical(name)) is not None


def check_key(name: object, what: str = "name") -> str:
    """The canonical form of `name`, a key; a ValueError naming it (and `what` it is) if not.

    `check_key("wet-pump", "device")` is `"wet_pump"`; `check_key("Dry Pump", "device")`
    raises "device 'Dry Pump' is not a key: ...".
    """
    if not isinstance(name, str) or not is_key(name):
        raise ValueError(f"{what} {name!r} is not a key: {GRAMMAR}")
    return canonical(name)


def check_address(address: object, what: str = "address") -> str:
    """The canonical form of a dotted address, every segment a key; else a ValueError naming it."""
    if not isinstance(address, str) or not address:
        raise ValueError(f"{what} {address!r} is not an address: dotted keys")
    segments = address.split(".")
    if not all(is_key(segment) for segment in segments):
        raise ValueError(f"{what} {address!r} is not an address: dotted keys, each {GRAMMAR}")
    return canonical(address)


def check_keys[V](mapping: Mapping[str, V], what: str = "name") -> dict[str, V]:
    """`mapping` with every key checked and made canonical, in order.

    Raises:
        ValueError: A key that is not one, or two that are the same key once
            canonical (`wet-pump` and `wet_pump`), naming both.
    """
    out: dict[str, V] = {}
    given: dict[str, str] = {}
    for name, value in mapping.items():
        key = check_key(name, what)
        if key in out:
            raise ValueError(
                f"{what} {name!r} and {given[key]!r} are the same name: `-` and `_` are one"
            )
        out[key] = value
        given[key] = name
    return out


def humanise(key: str) -> str:
    """The label a blank one resolves to: `key` in sentence case (D-086).

    Underscores (and dashes) become spaces, the first letter is capitalised, and the rest
    stays as written: `dry_pump_flow` -> "Dry pump flow", `tvoc` -> "Tvoc" (a driver that
    means "TVOC" declares it). The one fallback: devices, namespaces, signals, inputs,
    commands, controllers, the rig and a JSON Schema field's title all use it.
    """
    words = " ".join(part for part in re.split(r"[_-]+", key) if part)
    return words[:1].upper() + words[1:]


class Keyed[V](dict[str, V]):
    """A dict keyed by names, found by either spelling: every key is taken as `canonical`.

    The rig's devices, links and a device's signals: `devices["wet-pump"]` is
    `devices["wet_pump"]`. Stored keys are canonical; what is iterated is what was stored.
    """

    def __init__(self, items: Iterable[tuple[str, V]] = ()) -> None:
        super().__init__()
        for key, value in items:
            self[key] = value

    @classmethod
    def of(cls, mapping: Mapping[str, V]) -> Keyed[V]:
        """`mapping`'s items, keyed canonically."""
        return cls(mapping.items())

    def __getitem__(self, key: str) -> V:
        return super().__getitem__(canonical(key) if isinstance(key, str) else key)

    def __setitem__(self, key: str, value: V) -> None:
        super().__setitem__(canonical(key), value)

    def __delitem__(self, key: str) -> None:
        super().__delitem__(canonical(key) if isinstance(key, str) else key)

    def __contains__(self, key: object) -> bool:
        return super().__contains__(canonical(key) if isinstance(key, str) else key)

    def get(self, key: str, default: V | None = None) -> V | None:  # type: ignore[override]
        return super().get(canonical(key) if isinstance(key, str) else key, default)

    def pop(self, key: str, *default: V) -> V:  # type: ignore[override]
        return super().pop(canonical(key) if isinstance(key, str) else key, *default)

    def setdefault(self, key: str, default: V) -> V:  # type: ignore[override]
        return super().setdefault(canonical(key), default)


__all__ = [
    "GRAMMAR",
    "KEY",
    "Keyed",
    "canonical",
    "check_address",
    "check_key",
    "check_keys",
    "humanise",
    "is_key",
]
