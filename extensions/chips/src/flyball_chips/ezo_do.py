r"""Atlas Scientific EZO-DO: one probe, over UART or I2C, compensated for temperature/S/P.

Built from Atlas Scientific's published protocol documentation ("EZO-DO Embedded
Dissolved Oxygen Circuit", datasheet v5.8: https://files.atlas-scientific.com/DO_EZO_Datasheet.pdf),
not from a real device: this driver has never been run against a physical EZO-DO
circuit or probe.

**UART**, per the datasheet: default 38400 8N1, no flow control (p.10-11, "Default
state"/"UART mode"). Commands are ASCII strings terminated by a carriage return (`\\r`,
decimal 13); a single reading is `R\\r`, answered 600ms later ("Single reading mode",
p.19). The circuit's `O` command (p.28) enables or disables which parameters the `R`
reply carries -- dissolved oxygen in mg/L, and optionally percent saturation appended as
a second, comma-separated field -- and its factory default is `mg/L` alone (the UART
quick-reference table, p.15, lists `O`'s default state as `mg/L`). This driver decodes
exactly that default: a single mg/L reading, 2 decimal places (p.11's "Data format"
table), e.g. `"7.82\r"`. It does not decode the CSV `mg/L,%sat` form the `O` command can
turn on -- that is a rig-file misconfiguration relative to this driver's assumption, not
something it guesses at. The circuit's response codes share the `*` prefix and a `<CR>`
terminator; only `*OK` can be disabled, and it defaults to *enabled*, so a factory-default
circuit answers `R\r` with `*OK<CR>` followed by the reading frame. This driver does not
assume the response code has been turned off: it reads one frame, and if that frame is
the `*OK` acknowledgement, reads a second frame for the actual value. The datasheet gives
the D.O. range as 0.00-100 mg/L (p.1).

**I2C** (pp.29-52, "I2C mode"): the same commands, bare ASCII with no `\\r`, at the
default address 97 (0x61, p.29). The reply is a status byte followed by a NULL-terminated
string rather than a `<CR>`-terminated one; see `_ezo.py`. Which transport a circuit uses
is picked from the kind of `link` it is built on (`_ezo.EzoTransport`).

**Calibration** (`cal`, `cal,0`, `cal,clear`; UART p.20, I2C p.47): `cal` is a one-point
calibration to atmospheric oxygen saturation, `cal,0` a (separate, optional) zero-oxygen
point, `cal,clear` deletes both. These change the circuit's own stored calibration, not a
demand of this device (EZO-DO has none), so they are `@command`s without `writes=`. Each
waits its own processing delay (1.3s for `cal`/`cal,0`, 300ms for `clear`), so each is
`long=True`.

**Compensation** (temperature `T`/`RT`, salinity `S`, pressure `P`; UART pp.26-28, I2C
pp.49-51): D.O. solubility depends on all three. If this driver's optional `temperature`
input is bound and has a value, the combined `RT,<value>` command (900ms: set the
compensation temperature *and* take a reading in one round trip) replaces the plain `R`
(600ms); left unbound, the reading is uncompensated (the circuit's own default, 25 C).
`salinity` (default: conductivity in µS/cm, the `S,<value>`
form) and `pressure` (`P,<value>`, in kPa) are separate optional inputs, sent before the
read when bound and valued -- Atlas has no combined form for either, unlike temperature.
A bound input with no value leaves the D.O. with none either, carrying the input's quality,
rather than quietly reading uncompensated.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import (
    DriverConfig,
    Input,
    Node,
    NoValueError,
    Readable,
    Readout,
    Sample,
    command,
)
from flyball.foundation.errors import HardwareError, NotReadyError
from flyball.foundation.quantities import Quantity, Unit
from flyball.foundation.quantities.si import Celsius
from flyball.hardware.i2c import I2cLink
from flyball.hardware.uart import UartLink
from pydantic import Field

from flyball_chips._links import EzoLinkConfig

from ._ezo import READ_COMMAND, EzoTransport, compensation, decode_text

READ_DELAY_S = 0.6
"""The datasheet's single-reading mode: the D.O. frame follows 600ms later (UART p.19,
I2C p.46)."""
COMPENSATED_READ_DELAY_S = 0.9
"""`RT,<value>`: set the compensation temperature and read, in one round trip (I2C p.50)."""
TEMPERATURE_DELAY_S = 0.3
"""`T,<value>` (UART p.26, I2C p.49)."""
SALINITY_DELAY_S = 0.3
"""`S,<value>` (UART p.27, I2C p.51)."""
PRESSURE_DELAY_S = 0.3
"""`P,<value>` (UART p.28, I2C p.51)."""
CALIBRATE_DELAY_S = 1.3
"""`cal` / `cal,0` (I2C p.47)."""
CALIBRATE_CLEAR_DELAY_S = 0.3
"""`cal,clear` (I2C p.47)."""

DO_I2C_ADDRESS = 0x61
"""The default I2C address, 97 (DO datasheet p.29); irrelevant on UART."""

DISSOLVED_OXYGEN = Quantity("dissolved_oxygen", Unit.get("mg/L"))
TEMPERATURE = Quantity("temperature", Celsius)
SALINITY_COMPENSATION = Quantity("conductivity", Unit.get("µS/cm"))
"""`S,<value>`'s default form -- conductivity, not the alternate `S,<value>,ppt`."""
PRESSURE_COMPENSATION = Quantity("pressure", Unit.get("kPa"))


def parse_do(frame: bytes) -> float:
    """The dissolved-oxygen value, in mg/L, from a reply frame (a plain `R` or an `RT,<value>`).

    Assumes the circuit's `O` command is at its factory default (`mg/L` alone,
    not the CSV `mg/L,%sat` form).

    Raises:
        HardwareError: A `*`-prefixed status/error frame in place of a reading,
            a CSV reply (the `%sat` parameter has been enabled), or a frame that
            is not a decimal number.
    """
    text = decode_text(frame, "EZO-DO")
    if "," in text:
        raise HardwareError(
            f"EZO-DO reply {frame!r} is a multi-field CSV reading -- the O command "
            "must be at its factory default (mg/L alone) for this driver"
        )
    try:
        return float(text)
    except ValueError as exc:
        raise HardwareError(f"EZO-DO reply {frame!r} is not a decimal mg/L value") from exc


class EzoDoProbe:
    """One EZO-DO circuit, over UART or I2C: command, wait, read, decode."""

    __slots__ = ("transport",)

    def __init__(
        self, link: UartLink | I2cLink, address: int = DO_I2C_ADDRESS, sleep: bool = True
    ) -> None:
        self.transport = EzoTransport(link, "EZO-DO", address, sleep)

    def read(self) -> float:
        """The current D.O. in mg/L, uncompensated: one `R` command, one reading frame."""
        return parse_do(self.transport.read(READ_COMMAND, READ_DELAY_S))

    def read_at_temperature(self, celsius: float) -> float:
        """The current D.O. in mg/L: `RT,<celsius>`, setting the compensation and reading in one."""
        command = f"RT,{celsius}".encode("ascii")
        return parse_do(self.transport.read(command, COMPENSATED_READ_DELAY_S))

    def set_salinity(self, microsiemens: float) -> None:
        """`S,<microsiemens>`: the conductivity to compensate against."""
        self.transport.write(f"S,{microsiemens}".encode("ascii"), SALINITY_DELAY_S)

    def set_pressure(self, kpa: float) -> None:
        """`P,<kpa>`: the atmospheric pressure to compensate against."""
        self.transport.write(f"P,{kpa}".encode("ascii"), PRESSURE_DELAY_S)

    def calibrate(self) -> None:
        """`cal`: one-point calibration to atmospheric oxygen saturation."""
        self.transport.write(b"cal", CALIBRATE_DELAY_S)

    def calibrate_zero(self) -> None:
        """`cal,0`: the (separate, optional) zero-dissolved-oxygen point."""
        self.transport.write(b"cal,0", CALIBRATE_DELAY_S)

    def calibrate_clear(self) -> None:
        """`cal,clear`: deletes both calibration points."""
        self.transport.write(b"cal,clear", CALIBRATE_CLEAR_DELAY_S)


class EzoDo(Readable):
    """One EZO-DO probe on the device root: `dissolved_oxygen [RP]`; T/S/P inputs optional."""

    dissolved_oxygen = Readout(
        "dissolved_oxygen", quantity=DISSOLVED_OXYGEN, range=(0.0, 100.0), precision=2
    )
    temperature = Input("temperature", quantity=TEMPERATURE, optional=True)
    """Bound and valued: `RT,<value>` replaces the plain `R`. Unbound: the reading is
    uncompensated (the circuit's own default, 25 C)."""
    salinity = Input("salinity", quantity=SALINITY_COMPENSATION, optional=True)
    """Bound and valued: `S,<value>` (conductivity, µS/cm) is sent before the read."""
    pressure = Input("pressure", quantity=PRESSURE_COMPENSATION, optional=True)
    """Bound and valued: `P,<value>` (kPa) is sent before the read."""

    def __init__(
        self,
        name: str,
        link: UartLink | I2cLink,
        address: int = DO_I2C_ADDRESS,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.probe = EzoDoProbe(link, address, sleep)

    @property
    def config(self) -> EzoDoConfig:
        return EzoDoConfig(link="", address=self.probe.transport.address)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        try:
            given = compensation(self.temperature, self.salinity, self.pressure)
        except NoValueError as error:  # a bound input has no value: nor does the D.O.
            yield self.sample(time_ns, dissolved_oxygen=error.no_value)
            return
        except NotReadyError:  # a bound input has nothing yet: no reading this time
            return
        if "salinity" in given:
            self.probe.set_salinity(given["salinity"])
        if "pressure" in given:
            self.probe.set_pressure(given["pressure"])
        if "temperature" in given:
            value = self.probe.read_at_temperature(given["temperature"])
        else:
            value = self.probe.read()
        yield self.sample(time_ns, dissolved_oxygen=value)

    @command(long=True)
    def calibrate(self) -> None:
        """`cal`: one-point calibration to atmospheric oxygen saturation.

        Not `writes=`: it changes the probe's own stored calibration, not an output of
        this device.
        """
        self.probe.calibrate()

    @command(long=True)
    def calibrate_zero(self) -> None:
        """`cal,0`: the (separate, optional) zero-dissolved-oxygen point.

        Not `writes=`; see `calibrate`.
        """
        self.probe.calibrate_zero()

    @command(long=True)
    def calibrate_clear(self) -> None:
        """`cal,clear`: deletes both calibration points. Not `writes=`; see `calibrate`."""
        self.probe.calibrate_clear()


class EzoDoConfig(DriverConfig[EzoDo], type="ezo_do"):
    """One EZO-DO circuit, on its own UART or at an I2C `address` (default 0x61)."""

    link: EzoLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(
        default=DO_I2C_ADDRESS,
        ge=0x03,
        le=0x77,
        description="The I2C address; ignored when the circuit is wired for UART.",
    )

    def build(self, name: str, label: str | None = None) -> EzoDo:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return EzoDo(name, resolve(self.link), self.address, label=label)


EzoDo.config_type = EzoDoConfig  # the config is declared after the device it builds


__all__ = [
    "CALIBRATE_CLEAR_DELAY_S",
    "CALIBRATE_DELAY_S",
    "COMPENSATED_READ_DELAY_S",
    "DISSOLVED_OXYGEN",
    "DO_I2C_ADDRESS",
    "PRESSURE_COMPENSATION",
    "PRESSURE_DELAY_S",
    "READ_DELAY_S",
    "SALINITY_COMPENSATION",
    "SALINITY_DELAY_S",
    "TEMPERATURE",
    "TEMPERATURE_DELAY_S",
    "EzoDo",
    "EzoDoConfig",
    "EzoDoProbe",
    "parse_do",
]
