"""`driver: values`: numbers an operator enters, for other devices' inputs to follow.

```yaml
devices:
  bench:
    driver: values
    values:
      dry_supply: { initial: 36.5, unit: "%", label: Dry supply humidity }
  blender:
    inputs: { dry: bench.dry_supply, wet: 88.5 }
```

Each entry is a signal of the device: a `setting` with access `RPW`,
published from build with its `initial` (so an input bound to it is never
`pending`), written through the rig's ordinary write (`PUT
/api/signals/bench.dry_supply`, the `operate` tier), which clamps it to its
`limits`. The rig logs every write as a `value_written` event, records it when
a recording is running, and keeps the last written value, who wrote it and
when in the store's `live_value` table, so a restart restores it while the rig
file's `initial` is unchanged. The device has no demands and nothing to read:
nothing judges it stale, and a stop leaves it as it is. An input that never
changes binds to a plain number instead (`inputs: {wet: 88.5}`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..keys import check_keys
from ..quantities.quantity import Quantity
from ..quantities.si import Unitless
from .device import Committable, DriverConfig
from .signal import Access, Bounds, Role, SignalSpec


class ValueEntry(BaseModel):
    """One operator-entered value: its starting number, unit and what it may be set to."""

    model_config = ConfigDict(extra="forbid")

    initial: float = Field(
        description="Its value from build, and after a restart unless one was written since"
        " while this stayed the same."
    )
    unit: str | None = Field(
        default=None, description="The unit symbol (`%`, `°C`, `L/min`); omit for none."
    )
    quantity: str | None = Field(
        default=None, description="What it is (`humidity`); default: the value's name."
    )
    label: str = Field(default="", description="The display text; default: the titled name.")
    limits: Bounds | None = Field(
        default=None, description="What a write is clamped to, `[low, high]`, in its unit."
    )

    @field_validator("initial")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError(f"initial {value!r}: must be finite")
        return value


class Values(Committable):
    """Operator-entered numbers other devices' inputs follow: one `setting` [RPW] per value.

    Built from its entries; a write holds the value (nothing to put on any
    hardware). Published at build with each `initial`.
    """

    def __init__(
        self,
        name: str,
        values: Mapping[str, ValueEntry],
        label: str | None = None,
        config: ValuesConfig | None = None,
    ) -> None:
        super().__init__(name, label)
        self.entries = dict(values)
        self._config = config
        self.bind([_spec(key, entry) for key, entry in self.entries.items()])

    @property
    def config(self) -> ValuesConfig:
        """The config this was built from, or one describing it when built in code."""
        if self._config is not None:
            return self._config
        return ValuesConfig(values=self.entries)

    def initial(self, path: str) -> Any:
        """The rig file's `initial` in force for value `path`."""
        return self.entries[path].initial


def _spec(name: str, entry: ValueEntry) -> SignalSpec:
    quantity = (
        Quantity(entry.quantity or name, Unitless)
        if entry.unit is None
        else Quantity(entry.quantity or name, entry.unit)
    )
    return SignalSpec(
        name=name,
        quantity=quantity,
        access=Access.RPW,
        role=Role.SETTING,
        initial=entry.initial,
        label=entry.label,
        limits=entry.limits,
    )


class ValuesConfig(DriverConfig[Values], type="values"):
    """Operator-entered numbers, each a writable setting other devices' inputs may follow."""

    values: dict[str, ValueEntry] = Field(
        default_factory=dict, description="Each value by name: `{initial, unit, label, limits}`."
    )

    @field_validator("values", mode="before")
    @classmethod
    def _keys(cls, value: Any) -> Any:
        """Each value's name is a key, canonical: `dry-supply` is `dry_supply`."""
        return check_keys(value, "value") if isinstance(value, Mapping) else value

    def build(self, name: str, label: str | None = None) -> Values:
        return Values(name, self.values, label, self)


__all__ = ["ValueEntry", "Values", "ValuesConfig"]
