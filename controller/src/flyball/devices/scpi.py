"""SCPI instruments over a text link: a query per measurand, a command per demand.

`MEAS:VOLT:DC?` answers `+1.23456E-02`. A
[ScpiReader][flyball.devices.scpi.ScpiReader] is a table of such queries,
one per measurand; a [ScpiActuator][flyball.devices.scpi.ScpiActuator] is
one command with the demand formatted in. Replies parse as a float by
default; give `parse` for an instrument that answers `1.234 V`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from flyball.core.config import resolve
from flyball.core.device import DeviceConfig, DeviceSettings, DeviceState, command
from flyball.core.reading import Measurand, Reader, Sample, Source
from flyball.core.sink import Actuator, ActuatorState
from flyball.core.units.dimension import Unit
from flyball.hardware.links import TextLink, TextLinkConfig

Parser = Callable[[str], float]


def parse_float(reply: str) -> float:
    """The default parser: the first token, as a number. `+1.2E-3 V` -> 0.0012."""
    return float(reply.strip().split()[0].rstrip(","))


class ScpiMeasurand(BaseModel):
    """One thing the instrument can be asked for."""

    query: str
    unit: str
    label: str = ""
    range: tuple[float, float] | None = None
    precision: int | None = None
    warn: tuple[float, float] | None = None
    alarm: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ScpiState(DeviceState):
    values: dict[str, float] = field(default_factory=dict)
    """The last reply per measurand, parsed."""
    identity: str | None = None
    """`*IDN?`, if asked."""


class ScpiReader(Reader):
    """Polls a table of SCPI queries as one source.

    Args:
        name: The reader's name; also the source's unless `source` is given.
        link: What to talk over.
        measurands: `{name: ScpiMeasurand}`: the queries and their units.
        parse: How a reply becomes a number.
    """

    def __init__(
        self,
        name: str,
        link: TextLink,
        measurands: Mapping[str, ScpiMeasurand],
        parse: Parser = parse_float,
        source: str | None = None,
    ) -> None:
        self.link = link
        self.parse = parse
        self.table = dict(measurands)
        self.measurands = {
            key: Measurand(
                key, Unit.get(m.unit), m.label, m.range, m.precision, warn=m.warn, alarm=m.alarm
            )
            for key, m in measurands.items()
        }
        self.source = Source(source or name, self.measurands.values())
        super().__init__(name, (self.source,))
        self._values: dict[str, float] = {}
        self._identity: str | None = None

    @property
    def state(self) -> ScpiState:
        return ScpiState(values=dict(self._values), identity=self._identity)

    def read(self, time_ns: int) -> Iterable[Sample]:
        values = {
            self.measurands[key]: self.parse(self.link.query(m.query))
            for key, m in self.table.items()
        }
        self._values = {m.name: v for m, v in values.items()}
        return [Sample(self.source, self.source.next_seq(), time_ns, values)]

    @command
    def identify(self) -> str:
        """Ask the instrument what it is (`*IDN?`)."""
        self._identity = self.link.query("*IDN?")
        return self._identity

    @command
    def query(self, text: str) -> str:
        """Send any query and return the raw reply. For bring-up, not programs."""
        return self.link.query(text)


class ScpiReaderConfig(DeviceConfig[ScpiReader], tag="scpi_reader"):
    """A reader from a rig file: a link (by config, or by name in the rig) and a table."""

    name: str
    link: TextLinkConfig | str  # type: ignore[valid-type]
    measurands: dict[str, ScpiMeasurand]
    source: str | None = None

    def build(self) -> ScpiReader:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a link before building")
        return ScpiReader(self.name, resolve(self.link), self.measurands, source=self.source)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScpiActuatorSettings(DeviceSettings):
    command: str
    """The command sent on each demand, with `{value}` formatted in."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScpiActuatorState(ActuatorState):
    readback: float | None = None
    """What the instrument says it is doing, if `readback` was given."""


class ScpiActuator(Actuator):
    """Sets one instrument value from the loop's demand: `SOUR:VOLT {value}`.

    Args:
        name: The actuator's name.
        link: What to talk over.
        command: A format string with `{value}` for the demand.
        demand_unit: The unit the instrument expects, by symbol.
        readback: A query that reports the setting, for `state`.
    """

    def __init__(
        self,
        name: str,
        link: TextLink,
        command: str,
        demand_unit: str | None = None,
        readback: str | None = None,
        parse: Parser = parse_float,
    ) -> None:
        super().__init__(name)
        self.link = link
        self.command = command
        self.readback = readback
        self.parse = parse
        self._demand: float | None = None
        self._readback: float | None = None
        if demand_unit is not None:
            self.demand_unit = Unit.get(demand_unit)  # type: ignore[misc]  per instance, not per class

    @property
    def settings(self) -> ScpiActuatorSettings:
        return ScpiActuatorSettings(command=self.command)

    @property
    def state(self) -> ScpiActuatorState:
        return ScpiActuatorState(demand=self._demand, readback=self._readback)

    def set_demand(self, demand: float) -> float | None:
        self._demand = demand
        self.link.write(self.command.format(value=demand))
        if self.readback is not None:
            self._readback = self.parse(self.link.query(self.readback))
            return self._readback
        return None

    @command
    def write(self, text: str) -> None:
        """Send any command. For bring-up, not programs."""
        self.link.write(text)


class ScpiActuatorConfig(DeviceConfig[ScpiActuator], tag="scpi_actuator"):
    name: str
    link: TextLinkConfig | str  # type: ignore[valid-type]
    command: str = Field(description="Sent on each demand, with {value} formatted in.")
    demand_unit: str | None = None
    readback: str | None = None

    def build(self) -> ScpiActuator:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a link before building")
        return ScpiActuator(
            self.name, resolve(self.link), self.command, self.demand_unit, self.readback
        )
