"""The rig file's envelope around one device: `driver:`, signal metadata, `inputs:`."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import MISSING, fields
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flyball.model.catalog import Catalogs, get_catalog

from ..errors import NotFoundError
from ..keys import canonical, check_address, check_key
from ..time.clock import Rate
from .device import Device, DriverConfig
from .novalue import OnNoValue
from .signal import KEEP, Access, Bounds, Keep, Node, NodeSpec, Role, Signal, SignalSpec


def _period(value: float | None, name: str | None) -> float | None:
    """A period in seconds, or None: refused unless finite and above zero."""
    if value is not None and not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} {value!r}: must be a finite number of seconds above zero")
    return value


def _backoff(value: list[float] | None) -> list[float] | None:
    """A backoff sequence: at least one wait, each finite and above zero."""
    if value is None:
        return value
    if not value:
        raise ValueError("backoff_s: give at least one wait")
    for wait in value:
        _period(wait, "backoff_s")
    return value


def _budget(value: int | None) -> int | None:
    if value is not None and value < 1:
        raise ValueError(f"fail_after {value!r}: must be at least 1")
    return value


class Reads(BaseModel):
    """A device's `reads:`: when failed reads put it offline, and how it retries.

    `fail_after` reads that raise in a row put the device `offline`; it is
    then read again after each wait in `backoff_s` in turn, the last one
    repeating, until a read succeeds. `give_up_after_s` stops the retries
    that long after `offline` was raised (null: never). A key left out is
    the runner's `reads:`, then the built-in default.
    """

    model_config = ConfigDict(extra="forbid")

    fail_after: int | None = Field(
        default=None, description="Reads that raise in a row before the device is offline."
    )
    backoff_s: list[float] | None = Field(
        default=None,
        description="Seconds between retries while offline, in turn; the last repeats.",
    )
    give_up_after_s: float | None = Field(
        default=None,
        description="Stop retrying this long after the device went offline; null: never.",
    )

    @field_validator("fail_after")
    @classmethod
    def _at_least_one(cls, value: int | None) -> int | None:
        return _budget(value)

    @field_validator("backoff_s")
    @classmethod
    def _waits(cls, value: list[float] | None) -> list[float] | None:
        return _backoff(value)

    @field_validator("give_up_after_s")
    @classmethod
    def _positive_seconds(cls, value: float | None, info: Any) -> float | None:
        return _period(value, info.field_name)


class SignalMeta(BaseModel):
    """A signal's metadata in the rig file, and the access to remove.

    `access` names the set to keep (`"r"`); `readable`, `published` and
    `writable` drop one flag each and take only `false` -- the driver
    declares what it can honour, the file cannot add to it. `limits` only
    narrows the driver's: a demand is clamped to both.

    A key left out leaves the driver's value; a key given as `null` clears
    it back to the unset default (`label: null` is the name humanised,
    `warning: null` no band). `limits: null` clears only the file's narrowing,
    never the driver's limits.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    range: Bounds | None = None
    precision: int | None = None
    warning: Bounds | None = None
    alarm: Bounds | None = None
    poll_s: float | None = None
    stale_after_s: float | None = None
    limits: Bounds | None = None
    max_rate: Rate | None = None
    on_no_value: OnNoValue | None = Field(
        default=None,
        description="A banded signal with no value because of a fault: fire (band_unknown) or"
        " ignore. Default: fire with an alarm band, ignore with only a warning band.",
    )
    tags: dict[str, str] | None = None
    """Groupings across the tree, `{axis: name}`, added to the driver's."""
    record: bool | None = Field(
        default=None,
        description="false: left out of a recording started with the default selection (a raw"
        " value a derived signal is computed from). Default: recorded.",
    )
    access: str | None = None
    readable: bool | None = None
    published: bool | None = None
    writable: bool | None = None

    @field_validator("range", "warning", "alarm", "limits")
    @classmethod
    def _finite_and_not_inverted(cls, value: Bounds | None, info: Any) -> Bounds | None:
        if value is None:
            return value
        lo, hi = value
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise ValueError(f"{info.field_name} {value!r}: must be finite")
        if lo > hi:
            raise ValueError(f"{info.field_name} {value!r}: inverted, low above high")
        return value

    @field_validator("poll_s", "stale_after_s")
    @classmethod
    def _positive_seconds(cls, value: float | None, info: Any) -> float | None:
        return _period(value, info.field_name)

    @field_validator("access")
    @classmethod
    def _wire_form(cls, value: str | None) -> str | None:
        return None if value is None else str(Access.parse(value))

    @field_validator("readable", "published", "writable")
    @classmethod
    def _only_removes(cls, value: bool | None) -> bool | None:
        if value:
            raise ValueError(
                "only `false` is allowed: the driver declares the access it can honour"
            )
        return value


class NamespaceMeta(BaseModel):
    """A namespace's metadata in the rig file: label, period, and the metadata of what is under it.

    A namespace's own driver settings (an I²C address) are not here: the
    driver declares its namespaces in its own config, typed, and the
    envelope only sets metadata on what the driver declared.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    poll_s: float | None = None
    tags: dict[str, str] | None = None
    """Applied to every signal under the namespace; a signal's own win."""
    signals: dict[str, SignalMeta | NamespaceMeta] = Field(default_factory=dict)

    @field_validator("poll_s")
    @classmethod
    def _positive_seconds(cls, value: float | None, info: Any) -> float | None:
        return _period(value, info.field_name)

    @field_validator("signals", mode="before")
    @classmethod
    def _paths(cls, value: Any) -> Any:
        return _keyed_by_path(value, "signals")


NamespaceMeta.model_rebuild()


def _keyed_by_path(value: Any, what: str) -> Any:
    """A mapping keyed by paths under a device, each a dotted key, made canonical (D-079)."""
    if not isinstance(value, Mapping):
        return value
    out: dict[str, Any] = {}
    for path, item in value.items():
        key = check_address(path, f"{what} path")
        if key in out:
            raise ValueError(f"{what}.{path}: given twice: `-` and `_` are one")
        out[key] = item
    return out


class Permissive(BaseModel):
    """A demand's permissive: writes to it are refused unless `signal`'s value is in the band.

    `above` and `below` bound the value (either or both); a value with no reading, or no
    value (`stale`, `invalid`, `pending`), refuses -- a permissive fails closed. A write
    of the demand's resolved stop value, and a stop itself, are never refused.
    """

    model_config = ConfigDict(extra="forbid")

    signal: str = Field(description="The address whose value permits the write.")
    above: float | None = Field(default=None, description="Permit only above this value.")
    below: float | None = Field(default=None, description="Permit only below this value.")

    @model_validator(mode="after")
    def _a_bound(self) -> Permissive:
        if self.above is None and self.below is None:
            raise ValueError(f"permissive on {self.signal!r}: give `above`, `below` or both")
        for name in ("above", "below"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"permissive {name} {value!r}: must be finite")
        if self.above is not None and self.below is not None and self.above >= self.below:
            raise ValueError(
                f"permissive on {self.signal!r}: above {self.above} is not below {self.below}"
            )
        return self

    def holds(self, value: float) -> bool:
        """Whether `value` permits the write."""
        return (self.above is None or value > self.above) and (
            self.below is None or value < self.below
        )

    def describe(self) -> str:
        parts = [f"> {self.above:g}" if self.above is not None else ""]
        parts.append(f"< {self.below:g}" if self.below is not None else "")
        return f"{self.signal} {' and '.join(p for p in parts if p)}"


type OnShutdown = Literal["stop", "keep"]


class DeviceEntry(BaseModel):
    """The envelope of one device in the rig file: flyball's keys, the same for every driver.

    `driver:` picks the driver's config model by type; the driver's own
    fields sit flat beside these keys, and every key that is not one of them
    is the driver's (`driver_config`).
    """

    model_config = ConfigDict(extra="allow")

    driver: str
    label: str | None = None
    poll_s: float | None = None
    signals: dict[str, SignalMeta | NamespaceMeta] = Field(default_factory=dict)
    inputs: dict[str, str | float] = Field(
        default_factory=dict,
        description="Input name -> what it follows: an address on another device"
        " (`hum_sensors.dry.humidity`), or a number (`36.5`). Every input the driver declares"
        " must be given one; it has no default.",
    )
    """Input name -> an address on another device, or a number; the rig binds it."""
    reads: Reads | None = None
    """When failed reads put the device offline, and how it retries; the runner's otherwise."""
    retry_max_age_s: float | None = Field(
        default=None,
        description="How long a value a failed write kept may wait to be sent again; older is"
        " dropped, not sent. Omit for 60 s.",
    )
    stop: dict[str, float | Keep] | None = Field(
        default=None,
        description="Demand path -> what a stop writes to it: a number, or `keep` to leave it as"
        " it is. A demand left out takes its driver's `off`, else keeps. Refused on a device"
        " whose driver has a stop command (it stops the whole device).",
    )
    on_shutdown: OnShutdown | None = Field(
        default=None,
        description="What the runner's shutdown does to this device: `stop` (its resolved stop)"
        " or `keep` (writes nothing). Omit for the runner's `on_shutdown`.",
    )
    permissive: dict[str, Permissive] | None = Field(
        default=None,
        description="Demand path -> the value condition a write to it needs: `{signal: <address>,"
        " above: <n>, below: <n>}`. Fails closed when the signal has no value.",
    )

    @field_validator("stop")
    @classmethod
    def _finite_stops(cls, value: dict[str, float | Keep] | None) -> dict[str, float | Keep] | None:
        for path, stop in (value or {}).items():
            if stop != KEEP and not math.isfinite(float(stop)):
                raise ValueError(f"stop.{path}: {stop!r} is not finite")
        return value

    @field_validator("poll_s", "retry_max_age_s")
    @classmethod
    def _positive_seconds(cls, value: float | None, info: Any) -> float | None:
        return _period(value, info.field_name)

    @field_validator("inputs", mode="before")
    @classmethod
    def _addresses_or_numbers(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for name, source in value.items():
                if isinstance(source, bool) or not isinstance(source, (str, int, float)):
                    raise ValueError(
                        f"inputs.{name}: {source!r} is neither an address nor a number"
                    )
                if isinstance(source, float) and not math.isfinite(source):
                    raise ValueError(f"inputs.{name}: {source!r} is not finite")
                key = check_key(name, "input")
                if key in out:
                    raise ValueError(f"inputs.{name}: given twice: `-` and `_` are one")
                out[key] = canonical(source) if isinstance(source, str) else source
            return out
        return value

    @field_validator("signals", "stop", "permissive", mode="before")
    @classmethod
    def _paths(cls, value: Any, info: Any) -> Any:
        return _keyed_by_path(value, info.field_name)

    @model_validator(mode="before")
    @classmethod
    def _flat(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and "config" in data:
            raise ValueError(
                "`config` is not a device key: the driver's fields sit flat beside `driver:`"
            )
        return data

    @property
    def driver_config(self) -> dict[str, Any]:
        """The driver's own fields: every key of the entry that is not the envelope's."""
        return dict(self.model_extra or {})

    def build(
        self,
        name: str,
        links: Mapping[str, Any] | None = None,
        catalogs: Catalogs | None = None,
    ) -> Device:
        """Build the device `driver` describes and apply this envelope to it.

        The driver binds its tree; the signal metadata is then applied onto the
        bound objects in place, so nothing holds a stale reference. Unknown
        names and added access are errors that name the address. When the
        driver config's `link` names a key in `links`, it is substituted with
        the built object first; an undeclared name is a `NotFoundError`
        naming the device and the link.

        Args:
            name: The device's name in the rig.
            links: Name -> built link, for a config whose `link` names one.
            catalogs: Where `driver` is looked up. Default:
                [get_catalog][flyball.model.catalog.get_catalog] -- the
                process's installed `Catalogs`, set once by `flyball-runner`.
        """
        catalogs = catalogs or get_catalog()
        driver = catalogs.devices.get(self.driver)
        if driver is None:
            raise ValueError(f"driver {self.driver!r} is not registered")
        if not issubclass(driver, DriverConfig):
            raise ValueError(f"driver {self.driver!r} is a {driver.__name__}, not a device driver")
        config = driver.model_validate(self.driver_config)
        if isinstance(config.link, str):
            if links is None or config.link not in links:
                raise NotFoundError(f"device {name!r}: link {config.link!r} is not declared")
            config = config.model_copy(update={"link": links[config.link]})
        device = config.build(name, self.label)
        if self.label is not None:
            device.declared_label = self.label or None
        if self.poll_s is not None:
            device.poll_s = self.poll_s
        _set_meta_under(device.root, self.signals)
        _set_stops(device, self.stop or {})
        for path in self.permissive or {}:
            _writable_demand(device, path, "permissive")
        return device


def _writable_demand(device: Device, path: str, key: str) -> Signal:
    """The writable demand at `path` on `device`, for the entry's `key`; else a ValueError."""
    signal = device.signals.get(path)
    if signal is None:
        raise ValueError(f"{key}.{path}: {path!r} is not a signal of {device.name!r}")
    if signal.role is not Role.DEMAND or Access.W not in signal.access:
        raise ValueError(
            f"{key}.{path}: '{signal.address}' [{signal.access}] is not a writable demand"
        )
    return signal


def _set_stops(device: Device, stops: Mapping[str, float | Keep]) -> None:
    """The rig file's `stop:` onto each demand, checked: a writable demand, inside static limits.

    Refused whole on a device whose driver stops it with a command: its stop is that
    command, and a value beside it would never be written (or would fight it).
    """
    if stops and (command := device.stops_by()) is not None:
        raise ValueError(
            f"stop: {device.name!r} is stopped by its driver's {command!r} command; per-signal"
            " stop values are refused on it"
        )
    for path, value in stops.items():
        signal = _writable_demand(device, path, "stop")
        if value != KEEP:
            number = float(value)
            low, high = _static_bounds(signal)
            if (low is not None and number < low) or (high is not None and number > high):
                raise ValueError(
                    f"stop.{path}: {number:g} is outside '{signal.address}' limits"
                    f" [{'' if low is None else f'{low:g}'}, {'' if high is None else f'{high:g}'}]"
                )
            value = number
        signal.stop = value


def _static_bounds(signal: Signal) -> tuple[float | None, float | None]:
    """The numeric ends of `signal`'s limits (a bound that follows a signal is not static)."""
    low = high = None
    if (declared := signal.spec.limits) is not None:
        low = declared[0] if isinstance(declared[0], (int, float)) else None
        high = declared[1] if isinstance(declared[1], (int, float)) else None
    if (narrowed := signal.narrowed) is not None:
        low = narrowed[0] if low is None else max(low, narrowed[0])
        high = narrowed[1] if high is None else min(high, narrowed[1])
    return low, high


_SIGNAL_FIELDS = (
    "label",
    "range",
    "precision",
    "warning",
    "alarm",
    "poll_s",
    "stale_after_s",
    "max_rate",
    "on_no_value",
    "record",
)
_NODE_FIELDS = ("label", "poll_s", "tags")


def _default(spec: type[Any], name: str) -> Any:
    """What `spec` (a `SignalSpec` or `NodeSpec`) holds for `name` when nothing sets it."""
    for f in fields(spec):
        if f.name == name:
            if f.default is not MISSING:
                return f.default
            if f.default_factory is not MISSING:
                return f.default_factory()
    raise KeyError(name)  # pragma: no cover -- the field lists name only defaulted fields


def _changes(meta: BaseModel, names: tuple[str, ...], spec: type[Any]) -> dict[str, Any]:
    """The fields the file set: a value as given, an explicit `null` as the spec's default.

    A key the file left out is not here, so the driver's value stands.
    """
    return {
        name: _default(spec, name) if (value := getattr(meta, name)) is None else value
        for name in names
        if name in meta.model_fields_set
    }


def _set_meta_under(node: Node, metas: Mapping[str, SignalMeta | NamespaceMeta]) -> None:
    for name, meta in metas.items():
        address = f"{node.address}.{name}"
        if (signal := node.signals.get(name)) is not None:
            if isinstance(meta, NamespaceMeta):
                raise ValueError(f"'{address}' is a signal, not a namespace")
            _set_signal_meta(signal, meta)
        elif (child := node.children.get(name)) is not None:
            if isinstance(meta, SignalMeta):
                # `{poll_s: 5}` alone parses as a signal's metadata; on a
                # namespace it means the same thing.
                if extra := meta.model_fields_set - set(_NODE_FIELDS):
                    raise ValueError(
                        f"'{address}' is a namespace: {', '.join(sorted(extra))} is a signal's"
                    )
                meta = NamespaceMeta(**{f: getattr(meta, f) for f in meta.model_fields_set})
            changes = _changes(meta, ("label", "poll_s"), NodeSpec)
            if changes:
                child.set_meta(**changes)
            if meta.tags:
                for signal in child.walk():
                    signal.set_meta(tags={**meta.tags, **signal.spec.tags})
            _set_meta_under(child, meta.signals)
        else:
            raise ValueError(f"'{address}' is not a signal or namespace of {node.device.name!r}")


def _set_signal_meta(signal: Signal, meta: SignalMeta) -> None:
    changes = _changes(meta, _SIGNAL_FIELDS, SignalSpec)
    if meta.tags:
        changes["tags"] = {**signal.spec.tags, **meta.tags}
    if changes:
        signal.set_meta(**changes)
    if "limits" in meta.model_fields_set:
        signal.narrow(meta.limits)
    value = signal.access.value if meta.access is None else Access.parse(meta.access).value
    for flag, keep in (
        (Access.R, meta.readable),
        (Access.P, meta.published),
        (Access.W, meta.writable),
    ):
        if keep is False:
            value &= ~flag.value
    if value & Access.P.value and not value & Access.R.value:
        raise ValueError(f"Signal '{signal.address}': readable: false leaves it published")
    if value != signal.access.value:
        signal.restrict(Access(value))
