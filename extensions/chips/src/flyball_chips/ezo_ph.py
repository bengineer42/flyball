r"""Atlas Scientific EZO-pH: one probe, over UART or I2C, temperature-compensated.

Built from Atlas Scientific's published protocol documentation ("pH Circuit EZO",
datasheet v1.3: https://cdn.sparkfun.com/datasheets/Sensors/Biometric/pH_EZO_datasheet_v13.pdf),
not from a real device: this driver has never been run against a physical EZO-pH
circuit or probe.

**UART**, per the datasheet: default 38400 8N1, no flow control. Commands are ASCII
strings terminated by a carriage return (`\\r`, decimal 13); a single reading is `R\\r`,
answered `pH<CR>` (e.g. `"7.002\\r"`) one second later ("Single reading mode", p.16). The
circuit's seven response codes share the `*` prefix and a `<CR>` terminator (`*OK`, `*ER`,
`*OV`, `*UV`, `*RS`, `*RE`, `*SL`, `*WA`, p.21); only `*OK` can be disabled
(`RESPONSE,0\\r`), and it defaults to *enabled*, so a factory-default circuit answers
`R\\r` with `*OK<CR>` followed by the `pH<CR>` frame. This driver does not assume the
response code has been turned off: it reads one frame, and if that frame is the `*OK`
acknowledgement, reads a second frame for the actual value. Any other `*`-prefixed frame
(`*ER`, `*OV`, `*UV`, ...) is a protocol error, not a reading, and raises.

**I2C** (pp.29-39, "I2C mode"): the same commands, bare ASCII with no `\\r`, at the
default address 0x63 ("An I2C address can be any number from 1-127... 99(0x63)", p.29).
The reply is a status byte (1 success, 2 syntax error, 254 still processing, 255 no data)
followed by a NULL-terminated string rather than a `<CR>`-terminated one (p.30-31); see
`_ezo.py`. Which transport a circuit uses is picked from the kind of `link` it is built
on (`_ezo.EzoTransport`), not from a config flag.

**Calibration** (`cal,mid|low|high,<value>`, `cal,clear`, `cal,?`; UART pp.18-20, I2C
pp.36-38): the datasheet requires `cal,mid` (the pH-7 midpoint) to be done first; `low`
and `high` may follow in either order or be omitted. `cal,mid` after an existing
calibration clears the other points -- full calibration must be redone. These commands
change the circuit's own stored calibration, not a demand of this device (EZO-pH has
none), so they are `@command`s without `writes=`. Each waits its own processing delay
(1.3s for a calibration point, 300ms for `clear`/`?`), so each is `long=True`.

**Temperature compensation** (`T,<value>`; UART p.17, I2C p.35): pH varies with
temperature (the Nernst equation, p.3), and the circuit cannot read a temperature itself
-- "Another device must be used to read the temperature" (p.35). If this driver's
optional `temperature` input is bound and has a value, `T,<value>` is sent (300ms) before
every `R`; if not, the reading is taken uncompensated (the circuit's own default, 25 C)
and the device holds `uncompensated` so that is visible rather than silent. EZO-pH has no
combined "set temperature and read" command (unlike EZO-DO/EZO-EC's `RT,<value>`), so
compensation costs a second round trip per read.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import (
    DriverConfig,
    Input,
    Node,
    Readable,
    Readout,
    Sample,
    Severity,
    command,
)
from flyball.foundation.errors import HardwareError, NotReadyError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.dimensions import Fraction
from flyball.foundation.quantities.si import Celsius
from flyball.hardware.i2c import I2cLink
from flyball.hardware.uart import UartLink
from pydantic import Field

from flyball_chips._links import EzoLinkConfig

from ._ezo import READ_COMMAND, EzoTransport, decode_text

READ_DELAY_S = 1.0
"""The datasheet's single-reading mode: the pH frame follows one second later (UART p.16,
I2C p.34)."""
TEMPERATURE_DELAY_S = 0.3
"""`T,<value>` (UART p.17, I2C p.35)."""
CALIBRATE_DELAY_S = 1.3
"""`cal,mid`/`cal,low`/`cal,high` (UART p.19, I2C p.37)."""
CALIBRATE_CLEAR_DELAY_S = 0.3
"""`cal,clear` (UART p.19, I2C p.37)."""
CALIBRATION_QUERY_DELAY_S = 0.3
"""`cal,?` (UART p.19, I2C p.38)."""

PH_I2C_ADDRESS = 0x63
"""The default I2C address (pH datasheet p.29); irrelevant when the circuit is on UART."""

pH = Fraction.unit("pH", "pH", 1.0, scale=(0.0, 14.0))
PH = Quantity("pH", pH)
TEMPERATURE = Quantity("temperature", Celsius)


def parse_ph(frame: bytes) -> float:
    """The pH value from a `pH<CR>` reply frame.

    Raises:
        HardwareError: A `*`-prefixed status/error frame (`*ER`, `*OV`, `*UV`, ...)
            in place of a reading, or a frame that is not a decimal number.
    """
    text = decode_text(frame, "EZO-pH")
    try:
        return float(text)
    except ValueError as exc:
        raise HardwareError(f"EZO-pH reply {frame!r} is not a decimal pH value") from exc


def parse_calibration_status(frame: bytes) -> int:
    """0-3 calibration points set, from a `?CAL,n<CR>` reply to `cal,?`.

    Raises:
        HardwareError: The reply is not `?CAL,<0-3>`.
    """
    text = decode_text(frame, "EZO-pH")
    prefix = "?CAL,"
    if not text.startswith(prefix) or text[len(prefix) :] not in ("0", "1", "2", "3"):
        raise HardwareError(f"EZO-pH reply {frame!r} is not a '?CAL,<0-3>' calibration status")
    return int(text[len(prefix) :])


class EzoPhProbe:
    """One EZO-pH circuit, over UART or I2C: command, wait, read, decode."""

    __slots__ = ("transport",)

    def __init__(
        self, link: UartLink | I2cLink, address: int = PH_I2C_ADDRESS, sleep: bool = True
    ) -> None:
        self.transport = EzoTransport(link, "EZO-pH", address, sleep)

    def read(self) -> float:
        """The current pH: one `R` command, one reading frame (after any `*OK`)."""
        return parse_ph(self.transport.read(READ_COMMAND, READ_DELAY_S))

    def set_temperature(self, celsius: float) -> None:
        """`T,<celsius>`: the temperature to compensate the next reading against."""
        self.transport.write(f"T,{celsius}".encode("ascii"), TEMPERATURE_DELAY_S)

    def calibrate_mid(self, ph: float) -> None:
        """`cal,mid,<ph>`: the pH-7 midpoint, required before `low`/`high`."""
        self.transport.write(f"cal,mid,{ph}".encode("ascii"), CALIBRATE_DELAY_S)

    def calibrate_low(self, ph: float) -> None:
        """`cal,low,<ph>`: a low calibration point (pH 1-6)."""
        self.transport.write(f"cal,low,{ph}".encode("ascii"), CALIBRATE_DELAY_S)

    def calibrate_high(self, ph: float) -> None:
        """`cal,high,<ph>`: a high calibration point (pH 8-14)."""
        self.transport.write(f"cal,high,{ph}".encode("ascii"), CALIBRATE_DELAY_S)

    def calibrate_clear(self) -> None:
        """`cal,clear`: deletes every calibration point."""
        self.transport.write(b"cal,clear", CALIBRATE_CLEAR_DELAY_S)

    def calibration_status(self) -> int:
        """`cal,?`: how many calibration points are set now (0-3)."""
        return parse_calibration_status(self.transport.read(b"cal,?", CALIBRATION_QUERY_DELAY_S))


class EzoPh(Readable):
    """One EZO-pH probe on the device root: `ph [RP]`, one round trip; `temperature` optional."""

    ph = Readout("ph", quantity=PH, range=(0.0, 14.0), precision=3)
    temperature = Input("temperature", quantity=TEMPERATURE, optional=True)
    """Bound (`inputs: {temperature: <address>}`, or a number): sent as `T,<value>` before
    each read. Unbound, or with no value: the reading is uncompensated (the circuit's own
    default, 25 C), and `uncompensated` is held so that is visible."""

    def __init__(
        self,
        name: str,
        link: UartLink | I2cLink,
        address: int = PH_I2C_ADDRESS,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.probe = EzoPhProbe(link, address, sleep)

    @property
    def config(self) -> EzoPhConfig:
        return EzoPhConfig(link="", address=self.probe.transport.address)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        try:
            celsius = self.temperature.value
        except NotReadyError:
            celsius = None
        if celsius is None:
            self.set_condition(
                "uncompensated",
                Severity.INFO,
                "reading without temperature compensation: the temperature input has no value",
            )
        else:
            self.clear_condition("uncompensated")
            self.probe.set_temperature(celsius)
        yield self.sample(time_ns, ph=self.probe.read())

    @command(long=True)
    def calibrate_mid(self, ph: float) -> None:
        """`cal,mid,<ph>`: the pH-7 midpoint, required before `calibrate_low`/`calibrate_high`.

        Not `writes=`: it changes the probe's own stored calibration, not an output of this
        device. Clears any calibration already done -- the datasheet's own rule.
        """
        self.probe.calibrate_mid(ph)

    @command(long=True)
    def calibrate_low(self, ph: float) -> None:
        """`cal,low,<ph>`: a low calibration point (pH 1-6).

        Not `writes=`; see `calibrate_mid`.
        """
        self.probe.calibrate_low(ph)

    @command(long=True)
    def calibrate_high(self, ph: float) -> None:
        """`cal,high,<ph>`: a high calibration point (pH 8-14).

        Not `writes=`; see `calibrate_mid`.
        """
        self.probe.calibrate_high(ph)

    @command(long=True)
    def calibrate_clear(self) -> None:
        """`cal,clear`: deletes every calibration point. Not `writes=`; see `calibrate_mid`."""
        self.probe.calibrate_clear()

    @command(long=True)
    def calibration_status(self) -> int:
        """`cal,?`: how many calibration points are set now (0-3)."""
        return self.probe.calibration_status()


class EzoPhConfig(DriverConfig[EzoPh], type="ezo_ph"):
    """One EZO-pH circuit, on its own UART or at an I2C `address` (default 0x63)."""

    link: EzoLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(
        default=PH_I2C_ADDRESS,
        ge=0x03,
        le=0x77,
        description="The I2C address; ignored when the circuit is wired for UART.",
    )

    def build(self, name: str, label: str | None = None) -> EzoPh:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return EzoPh(name, resolve(self.link), self.address, label=label)


EzoPh.config_type = EzoPhConfig  # the config is declared after the device it builds


__all__ = [
    "CALIBRATE_CLEAR_DELAY_S",
    "CALIBRATE_DELAY_S",
    "CALIBRATION_QUERY_DELAY_S",
    "PH",
    "PH_I2C_ADDRESS",
    "READ_DELAY_S",
    "TEMPERATURE",
    "TEMPERATURE_DELAY_S",
    "EzoPh",
    "EzoPhConfig",
    "EzoPhProbe",
    "pH",
    "parse_calibration_status",
    "parse_ph",
]
