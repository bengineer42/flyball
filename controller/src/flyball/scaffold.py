"""Starting points for a device of your own: `flyball new actuator NAME`.

Each template is a complete module that imports, schema-checks and registers
a tag, so the generated file works in a rig file before a line is changed.
"""

from __future__ import annotations

import keyword
import re
from pathlib import Path
from string import Template

__all__ = ["KINDS", "render", "write"]

_ACTUATOR = Template('''"""${Title}: an actuator built for this rig.

Registered as `tag = "${tag}"`, so a rig file can declare one:

    [[actuators]]
    tag = "${tag}"
    name = "${name}"
"""

from __future__ import annotations

from dataclasses import dataclass

from flyball.core.device import DeviceConfig, DeviceSettings
from flyball.core.sink import Actuator, ActuatorState, command
from flyball.core.units import Unit


@dataclass(frozen=True, slots=True, kw_only=True)
class ${Title}Settings(DeviceSettings):
    """What an operator or program may change while it runs."""

    limits: tuple[float, float] = (0.0, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class ${Title}State(ActuatorState):
    """What the process is doing now. `demand` and `conditions` come from the base."""

    output: float = 0.0


class ${Title}(Actuator):
    """TODO: what this drives, and what its demand means."""

    def __init__(self, name: str, limits: tuple[float, float] = (0.0, 1.0)) -> None:
        super().__init__(name)
        self.limits = limits
        self._demand: float | None = None
        self._output = 0.0
        self.demand_unit = Unit.get("°C")  # TODO: the unit a loop's demand arrives in

    @property
    def settings(self) -> ${Title}Settings:
        return ${Title}Settings(limits=self.limits)

    @property
    def state(self) -> ${Title}State:
        return ${Title}State(demand=self._demand, output=self._output)

    def set_demand(self, demand: float) -> None:
        """Called by the loop on every tick, under the rig's lock. Keep it quick."""
        self._demand = demand
        low, high = self.limits
        self._output = min(high, max(low, demand))
        # TODO: write self._output to the hardware

    @command
    def set_limits(self, low: float, high: float) -> ${Title}Settings:
        """Bound the output. Becomes `POST /api/actuators/${name}/set_limits`."""
        if high <= low:
            raise ValueError("high must exceed low")
        self.limits = (low, high)
        return self.settings


class ${Title}Config(DeviceConfig[${Title}], tag="${tag}"):
    """The rig-file entry. Fields here are what a builder writes down."""

    name: str = "${name}"
    limits: tuple[float, float] = (0.0, 1.0)

    def build(self) -> ${Title}:
        return ${Title}(self.name, self.limits)
''')

_READER = Template('''"""${Title}: a reader built for this rig.

Registered as `tag = "${tag}"`, so a rig file can declare one:

    [[readers]]
    period_s = 1.0
    [readers.device]
    tag = "${tag}"
    name = "${name}"
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from flyball.core.device import DeviceConfig, DeviceState
from flyball.core.reading import Measurand, Reader, Sample, Source


@dataclass(frozen=True, slots=True, kw_only=True)
class ${Title}State(DeviceState):
    """What the device is doing now, beyond its readings."""

    last: float | None = None


class ${Title}(Reader):
    """TODO: what this measures, and how."""

    def __init__(self, name: str, measurand: str = "temperature", unit: str = "°C") -> None:
        self.measurand = Measurand(measurand, unit)
        self.source = Source(name, (self.measurand,))
        super().__init__(name, (self.source,))
        self._last: float | None = None

    @property
    def state(self) -> ${Title}State:
        return ${Title}State(last=self._last)

    def read(self, time_ns: int) -> Iterable[Sample]:
        """Called every `period_s` by the rig; return one sample per source."""
        self._last = 0.0  # TODO: read the hardware
        return [Sample(self.source, self.source.next_seq(), time_ns, {self.measurand: self._last})]


class ${Title}Config(DeviceConfig[${Title}], tag="${tag}"):
    """The rig-file entry."""

    name: str = "${name}"
    measurand: str = "temperature"
    unit: str = "°C"

    def build(self) -> ${Title}:
        return ${Title}(self.name, self.measurand, self.unit)
''')

KINDS = {"actuator": _ACTUATOR, "reader": _READER}


def _identifier(name: str) -> str:
    snake = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if not snake or snake[0].isdigit() or keyword.iskeyword(snake):
        raise ValueError(f"{name!r} does not make a Python identifier")
    return snake


def render(kind: str, name: str) -> str:
    """The module text for a `kind` called `name`; the tag is `name`, the class its CamelCase."""
    if kind not in KINDS:
        raise ValueError(f"no template for {kind!r}; there are {sorted(KINDS)}")
    snake = _identifier(name)
    title = "".join(part.capitalize() for part in snake.split("_"))
    return KINDS[kind].substitute(Title=title, tag=snake, name=snake)


def write(kind: str, name: str, directory: str | Path = ".") -> Path:
    """Render into `<directory>/<name>.py`; refuses to overwrite."""
    path = Path(directory) / f"{_identifier(name)}.py"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(render(kind, name))
    return path
