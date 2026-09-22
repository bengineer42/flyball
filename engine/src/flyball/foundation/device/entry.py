"""The rig file's envelope around one device: `driver:`, per-signal overrides, `bound:`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from flyball.model.catalog import Catalogs, get_catalog

from ..errors import NotFoundError
from ..time.clock import Rate
from .device import ENVELOPE_KEYS, Device, DriverConfig
from .signal import Access, Band, Node, Signal


class SignalOverride(BaseModel):
    """The envelope's per-signal keys: metadata to override, access to remove.

    `access` names the set to keep (`"r"`); `readable`, `publishing` and
    `writable` drop one flag each and take only `false` -- the driver
    declares what it can honour, the file cannot add to it.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    range: Band | None = None
    precision: int | None = None
    warn: Band | None = None
    alarm: Band | None = None
    poll_s: float | None = None
    stale_after: float | None = None
    limits: Band | None = None
    max_rate: Rate | None = None
    tags: dict[str, str] | None = None
    """Groupings across the tree, `{axis: name}`, added to the driver's."""
    access: str | None = None
    readable: bool | None = None
    publishing: bool | None = None
    writable: bool | None = None

    @field_validator("access")
    @classmethod
    def _wire_form(cls, value: str | None) -> str | None:
        return None if value is None else str(Access.parse(value))

    @field_validator("readable", "publishing", "writable")
    @classmethod
    def _only_removes(cls, value: bool | None) -> bool | None:
        if value:
            raise ValueError(
                "only `false` is allowed: the driver declares the access it can honour"
            )
        return value


class NamespaceOverride(BaseModel):
    """The envelope of a namespace: label, period and the overrides of what is under it.

    A namespace's own driver settings (an I²C address) are not here: the
    driver declares its namespaces in its own config, typed, and the
    envelope only overrides what the driver declared.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    poll_s: float | None = None
    tags: dict[str, str] | None = None
    """Applied to every signal under the namespace; a signal's own win."""
    signals: dict[str, SignalOverride | NamespaceOverride] = Field(default_factory=dict)


NamespaceOverride.model_rebuild()


class DeviceEntry(BaseModel):
    """The envelope of one device in the rig file: flyball's keys, the same for every driver.

    `driver:` picks the driver's config model by tag; the driver's own
    settings sit flat beside these keys or under `config`, and both parse to
    the same thing. If `config` is present it is the whole of the driver
    config and any other leftover key is an error.
    """

    model_config = ConfigDict(extra="forbid")

    driver: str
    label: str | None = None
    poll_s: float | None = None
    signals: dict[str, SignalOverride | NamespaceOverride] = Field(default_factory=dict)
    bound: dict[str, str] = Field(default_factory=dict)
    """Role -> address on another device; the rig resolves it."""
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _flat_or_layered(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        leftover = {key: value for key, value in data.items() if key not in ENVELOPE_KEYS}
        if not leftover:
            return data
        if "config" in data:
            raise ValueError(
                f"{', '.join(sorted(leftover))} beside `config`: the driver's settings go"
                " under `config` or flat beside the envelope, not both"
            )
        envelope = {key: value for key, value in data.items() if key in ENVELOPE_KEYS}
        return {**envelope, "config": leftover}

    def build(
        self,
        name: str,
        links: Mapping[str, Any] | None = None,
        catalogs: Catalogs | None = None,
    ) -> Device:
        """Build the device `driver` describes and apply this envelope to it.

        The driver binds its tree; the overrides are then applied onto the
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
        config = driver.model_validate(self.config)
        if isinstance(config.link, str):
            if links is None or config.link not in links:
                raise NotFoundError(f"device {name!r}: link {config.link!r} is not declared")
            config = config.model_copy(update={"link": links[config.link]})
        device = config.build(name, self.label)
        if self.label is not None:
            device.label = self.label
        if self.poll_s is not None:
            device.poll_s = self.poll_s
        _override_under(device.root, self.signals)
        return device


_SIGNAL_FIELDS = (
    "label",
    "range",
    "precision",
    "warn",
    "alarm",
    "poll_s",
    "stale_after",
    "limits",
    "max_rate",
)
_NODE_FIELDS = ("label", "poll_s", "tags")


def _override_under(
    node: Node, overrides: Mapping[str, SignalOverride | NamespaceOverride]
) -> None:
    for name, override in overrides.items():
        address = f"{node.address}.{name}"
        if (signal := node.signals.get(name)) is not None:
            if isinstance(override, NamespaceOverride):
                raise ValueError(f"'{address}' is a signal, not a namespace")
            _override_signal(signal, override)
        elif (child := node.children.get(name)) is not None:
            if isinstance(override, SignalOverride):
                # `{poll_s: 5}` alone parses as a signal's override; on a
                # namespace it means the same thing.
                if extra := override.model_fields_set - set(_NODE_FIELDS):
                    raise ValueError(
                        f"'{address}' is a namespace: {', '.join(sorted(extra))} is a signal's"
                    )
                override = NamespaceOverride(
                    label=override.label, poll_s=override.poll_s, tags=override.tags
                )
            changes = {f: v for f in _NODE_FIELDS if (v := getattr(override, f)) is not None}
            if changes:
                child.override(**changes)
            if override.tags:
                for signal in child.walk():
                    signal.override(tags={**override.tags, **signal.spec.tags})
            _override_under(child, override.signals)
        else:
            raise ValueError(f"'{address}' is not a signal or namespace of {node.device.name!r}")


def _override_signal(signal: Signal, override: SignalOverride) -> None:
    changes = {f: v for f in _SIGNAL_FIELDS if (v := getattr(override, f)) is not None}
    if override.tags:
        changes["tags"] = {**signal.spec.tags, **override.tags}
    if changes:
        signal.override(**changes)
    value = signal.access.value if override.access is None else Access.parse(override.access).value
    for flag, keep in (
        (Access.R, override.readable),
        (Access.P, override.publishing),
        (Access.W, override.writable),
    ):
        if keep is False:
            value &= ~flag.value
    if value & Access.P.value and not value & Access.R.value:
        raise ValueError(f"Signal '{signal.address}': readable: false leaves it publishing")
    if value != signal.access.value:
        signal.restrict(Access(value))
