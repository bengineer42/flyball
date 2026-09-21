r"""Atlas Scientific EZO-EC: one probe, one ASCII command/response cycle over UART.

Built from Atlas Scientific's published protocol documentation ("EZO-EC Embedded
Conductivity Circuit", datasheet v6.7: https://files.atlas-scientific.com/EC_EZO_Datasheet.pdf),
not from a real device: this driver has never been run against a physical EZO-EC
circuit or probe.

Protocol, per the datasheet: default UART is 38400 8N1, no flow control (p.12-13,
"Default state"/"UART mode"). Commands are ASCII strings terminated by a carriage
return (`\\r`, decimal 13); a single reading is `R\\r`, answered 600ms later
("Single reading mode", p.21; "EC reading time" p.1). The circuit's `O` command
(p.28, UART quick reference) enables or disables which of four derived
parameters the `R` reply carries, in a fixed order -- `EC,TDS,SAL,SG` -- and its
factory default is **all four enabled** (`O ... default state: all enabled`,
same quick-reference table). This driver decodes that default: a 4-field CSV
reply, 3 decimal places (p.13's "Data format" table), e.g.
`"1413.000,706.500,0.700,1.000\r"`.

[Unverified]: p.21's own worked example under "Single reading mode" shows a
bare `"1,413\r"` reply to `R\r` -- a single value, not the 4-field CSV the same
document's UART quick-reference table (p.17) says `O` defaults to. That example
page is unchanged from older EZO-EC datasheet revisions and looks like it was
not updated for the current one; this driver follows the quick-reference table
(the more specific, and more recently revised, of the two) and treats the
factory-default reply as the 4-field CSV. If a real EZO-EC circuit only ever
sends the bare EC value at power-on, that is a live discrepancy to check against
hardware, not something resolvable from the datasheet alone.

`O` also accepts a `TDS` conversion-factor command (default 0.54, applied to EC
to derive TDS in ppm) and separate enable/disable of each of the four
parameters; none of that is modelled here -- this driver only decodes the
factory-default, all-four-enabled reply.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Output, Readable, Sample
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity, Unit
from flyball.foundation.quantities.dimensions import Fraction
from flyball.hardware.uart import UartLink

from flyball_chips._links import UartLinkConfig

from ._ezo import decode_text, read_frame

READ_DELAY_S = 0.6
"""The datasheet's single-reading mode: the EC,TDS,SAL,SG frame follows 600ms later (p.21)."""

CONDUCTIVITY = Quantity("conductivity", Unit.get("µS/cm"))
TOTAL_DISSOLVED_SOLIDS = Quantity("total_dissolved_solids", Unit.get("ppm"))
salinity_unit = Fraction.unit("PSU", "PSU", 1.0, scale=(0.0, 42.0))
SALINITY = Quantity("salinity", salinity_unit)
specific_gravity_unit = Fraction.unit("specific gravity", "SG", 1.0, scale=(1.0, 1.3))
SPECIFIC_GRAVITY = Quantity("specific_gravity", specific_gravity_unit)


def parse_ec(frame: bytes) -> tuple[float, float, float, float]:
    r"""`(ec, tds, sal, sg)` from a factory-default, 4-field `EC,TDS,SAL,SG\r` reply.

    Raises:
        HardwareError: A `*`-prefixed status/error frame in place of a reading,
            a reply that is not exactly 4 comma-separated fields (the `O`
            command is not at its factory default), or a field that is not a
            decimal number.
    """
    text = decode_text(frame, "EZO-EC")
    parts = text.split(",")
    if len(parts) != 4:
        raise HardwareError(
            f"EZO-EC reply {frame!r} is not a 4-field EC,TDS,SAL,SG reading -- the O "
            "command must be at its factory default (all four parameters enabled) "
            "for this driver"
        )
    try:
        ec, tds, sal, sg = (float(p) for p in parts)
    except ValueError as exc:
        raise HardwareError(f"EZO-EC reply {frame!r} has a non-decimal field") from exc
    return ec, tds, sal, sg


class EzoEcProbe:
    """One EZO-EC circuit on its own UART: command, wait, read, decode."""

    __slots__ = ("link", "sleep")

    def __init__(self, link: UartLink, sleep: bool = True) -> None:
        self.link = link
        self.sleep = sleep
        """Whether to wait the datasheet's 600ms reading time; off against a fake."""

    def read(self) -> tuple[float, float, float, float]:
        """`(ec, tds, sal, sg)`: one `R` command, one reading frame (after any `*OK`)."""
        frame = read_frame(self.link, self.sleep, READ_DELAY_S)
        return parse_ec(frame)


class EzoEc(Readable):
    """One EZO-EC probe on the device root: the four `O`-default outputs, one UART round trip."""

    conductivity = Output(
        "conductivity", quantity=CONDUCTIVITY, range=(0.07, 500_000.0), precision=3
    )
    total_dissolved_solids = Output(
        "total_dissolved_solids",
        quantity=TOTAL_DISSOLVED_SOLIDS,
        range=(0.0, 270_000.0),
        precision=3,
    )
    salinity = Output("salinity", quantity=SALINITY, range=(0.0, 42.0), precision=3)
    specific_gravity = Output(
        "specific_gravity", quantity=SPECIFIC_GRAVITY, range=(1.0, 1.3), precision=3
    )

    def __init__(
        self,
        name: str,
        link: UartLink,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.probe = EzoEcProbe(link, sleep)

    @property
    def config(self) -> EzoEcConfig:
        return EzoEcConfig(link="")

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        ec, tds, sal, sg = self.probe.read()
        yield self.sample(
            time_ns,
            conductivity=ec,
            total_dissolved_solids=tds,
            salinity=sal,
            specific_gravity=sg,
        )


class EzoEcConfig(DriverConfig[EzoEc], tag="ezo_ec"):
    """One EZO-EC circuit, alone on its UART."""

    link: UartLinkConfig | str  # type: ignore[valid-type]

    def build(self, name: str, label: str | None = None) -> EzoEc:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return EzoEc(name, resolve(self.link), label=label)


EzoEc.config_type = EzoEcConfig  # the config is declared after the device it builds


__all__ = [
    "CONDUCTIVITY",
    "READ_DELAY_S",
    "SALINITY",
    "SPECIFIC_GRAVITY",
    "TOTAL_DISSOLVED_SOLIDS",
    "EzoEc",
    "EzoEcConfig",
    "EzoEcProbe",
    "parse_ec",
]
