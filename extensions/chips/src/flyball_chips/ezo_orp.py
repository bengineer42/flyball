r"""Atlas Scientific EZO-ORP: one probe, over UART or I2C. No temperature compensation.

Built from Atlas Scientific's published protocol documentation ("EZO-ORP
Embedded ORP Circuit", datasheet v5.2: https://files.atlas-scientific.com/ORP_EZO_Datasheet.pdf),
not from a real device: this driver has never been run against a physical EZO-ORP
circuit or probe.

**UART**, per the datasheet: default 38400 8N1, no flow control (p.11-12, "Default
state"/"UART mode"). Commands are ASCII strings terminated by a carriage return (`\\r`,
decimal 13); a single reading is `R\\r`, answered one ORP value in mV 800ms later
("Single reading mode", p.20, e.g. `"209.6\r"`), one decimal place (p.12's "Data format"
table). The circuit's response codes share the `*` prefix and a `<CR>` terminator; only
`*OK` can be disabled, and it defaults to *enabled*, so a factory-default circuit answers
`R\r` with `*OK<CR>` followed by the reading frame. This driver does not assume the
response code has been turned off: it reads one frame, and if that frame is the `*OK`
acknowledgement, reads a second frame for the actual value. The datasheet gives the ORP
range as -1020mV to +1020mV (p.1); an "extended ORP scale" command (`ORPext`) widens this
but is disabled by default and not modelled here.

**I2C** (pp.29-43, "I2C mode"): the same commands, bare ASCII with no `\\r`, at the
default address 98 (0x62, p.29). The reply is a status byte followed by a NULL-terminated
string rather than a `<CR>`-terminated one; see `_ezo.py`. Which transport a circuit uses
is picked from the kind of `link` it is built on (`_ezo.EzoTransport`). [Unverified]: the
I2C datasheet's own `R` timing diagram (p.34) shows 900ms rather than UART's 800ms for the
same command; this driver uses the longer figure for both transports (`READ_DELAY_S`),
which can only ever wait longer than a transport strictly needs, never read back before
the circuit is ready.

**Calibration** (`cal,<value>`, `cal,clear`; UART/I2C pp.15-18): `cal,<value>` calibrates
to any known ORP reference in mV (there is no low/mid/high split, unlike EZO-pH); `cal,clear`
deletes it. Neither is `writes=`: it changes the probe's own stored calibration, not an
output of this device (EZO-ORP has none). Each waits its own processing delay (900ms for
`cal,<value>`, 300ms for `clear`), so each is `long=True`.

**No temperature compensation**: unlike EZO-pH/DO/EC, the EZO-ORP datasheet has no `T`/`RT`
command -- ORP millivolt readings are not temperature-corrected by the circuit, so this
driver declares no `temperature` input.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample, command
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity, Unit
from flyball.hardware.i2c import I2cLink
from flyball.hardware.uart import UartLink
from pydantic import Field

from flyball_chips._links import EzoLinkConfig

from ._ezo import READ_COMMAND, EzoTransport, decode_text

READ_DELAY_S = 0.9
"""The reading follows 800ms later on UART (p.20) but 900ms on I2C (p.34); the longer of
the two is used for both transports (see the module docstring)."""
CALIBRATE_DELAY_S = 0.9
"""`cal,<value>` (I2C p.36)."""
CALIBRATE_CLEAR_DELAY_S = 0.3
"""`cal,clear` (I2C p.37)."""

ORP_I2C_ADDRESS = 0x62
"""The default I2C address, 98 (ORP datasheet p.29); irrelevant on UART."""

ORP = Quantity("orp", Unit.get("mV"))


def parse_orp(frame: bytes) -> float:
    """The ORP value, in mV, from a reply frame.

    Raises:
        HardwareError: A `*`-prefixed status/error frame in place of a reading,
            or a frame that is not a decimal number.
    """
    text = decode_text(frame, "EZO-ORP")
    try:
        return float(text)
    except ValueError as exc:
        raise HardwareError(f"EZO-ORP reply {frame!r} is not a decimal mV value") from exc


class EzoOrpProbe:
    """One EZO-ORP circuit, over UART or I2C: command, wait, read, decode."""

    __slots__ = ("transport",)

    def __init__(
        self, link: UartLink | I2cLink, address: int = ORP_I2C_ADDRESS, sleep: bool = True
    ) -> None:
        self.transport = EzoTransport(link, "EZO-ORP", address, sleep)

    def read(self) -> float:
        """The current ORP in mV: one `R` command, one reading frame (after any `*OK`)."""
        return parse_orp(self.transport.read(READ_COMMAND, READ_DELAY_S))

    def calibrate(self, mv: float) -> None:
        """`cal,<mv>`: calibrates to a known ORP reference solution, in mV."""
        self.transport.write(f"cal,{mv}".encode("ascii"), CALIBRATE_DELAY_S)

    def calibrate_clear(self) -> None:
        """`cal,clear`: deletes the calibration point."""
        self.transport.write(b"cal,clear", CALIBRATE_CLEAR_DELAY_S)


class EzoOrp(Readable):
    """One EZO-ORP probe on the device root: `orp [RP]`, one round trip."""

    orp = Readout("orp", quantity=ORP, range=(-1020.0, 1020.0), precision=1)

    def __init__(
        self,
        name: str,
        link: UartLink | I2cLink,
        address: int = ORP_I2C_ADDRESS,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.probe = EzoOrpProbe(link, address, sleep)

    @property
    def config(self) -> EzoOrpConfig:
        return EzoOrpConfig(link="", address=self.probe.transport.address)

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, orp=self.probe.read())

    @command(long=True)
    def calibrate(self, mv: float) -> None:
        """`cal,<mv>`: calibrates to a known ORP reference solution, in mV.

        Not `writes=`: it changes the probe's own stored calibration, not an output of
        this device.
        """
        self.probe.calibrate(mv)

    @command(long=True)
    def calibrate_clear(self) -> None:
        """`cal,clear`: deletes the calibration point. Not `writes=`; see `calibrate`."""
        self.probe.calibrate_clear()


class EzoOrpConfig(DriverConfig[EzoOrp], type="ezo_orp"):
    """One EZO-ORP circuit, on its own UART or at an I2C `address` (default 0x62)."""

    link: EzoLinkConfig | str  # type: ignore[valid-type]
    address: int = Field(
        default=ORP_I2C_ADDRESS,
        ge=0x03,
        le=0x77,
        description="The I2C address; ignored when the circuit is wired for UART.",
    )

    def build(self, name: str, label: str | None = None) -> EzoOrp:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return EzoOrp(name, resolve(self.link), self.address, label=label)


EzoOrp.config_type = EzoOrpConfig  # the config is declared after the device it builds


__all__ = [
    "CALIBRATE_CLEAR_DELAY_S",
    "CALIBRATE_DELAY_S",
    "ORP",
    "ORP_I2C_ADDRESS",
    "READ_DELAY_S",
    "EzoOrp",
    "EzoOrpConfig",
    "EzoOrpProbe",
    "parse_orp",
]
