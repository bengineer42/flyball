r"""Atlas Scientific EZO-ORP: one probe, one ASCII command/response cycle over UART.

Built from Atlas Scientific's published protocol documentation ("EZO-ORP
Embedded ORP Circuit", datasheet v5.2: https://files.atlas-scientific.com/ORP_EZO_Datasheet.pdf),
not from a real device: this driver has never been run against a physical EZO-ORP
circuit or probe.

Protocol, per the datasheet: default UART is 38400 8N1, no flow control (p.11-12,
"Default state"/"UART mode"). Commands are ASCII strings terminated by a carriage
return (`\\r`, decimal 13); a single reading is `R\\r`, answered one ORP value in
mV 800ms later ("Single reading mode", p.20, e.g. `"209.6\r"`), one decimal place
(p.12's "Data format" table). The circuit's response codes share the `*` prefix
and a `<CR>` terminator; only `*OK` can be disabled, and it defaults to *enabled*,
so a factory-default circuit answers `R\r` with `*OK<CR>` followed by the reading
frame. This driver does not assume the response code has been turned off: it
reads one frame, and if that frame is the `*OK` acknowledgement, reads a second
frame for the actual value. The datasheet gives the ORP range as -1020mV to
+1020mV (p.1); an "extended ORP scale" command (`ORPext`) widens this but is
disabled by default and not modelled here.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Output, Readable, Sample
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity, Unit
from flyball.hardware.uart import UartLink

from flyball_chips._links import UartLinkConfig

from ._ezo import decode_text, read_frame

READ_DELAY_S = 0.8
"""The datasheet's single-reading mode: the ORP frame follows 800ms later (p.20)."""

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
    """One EZO-ORP circuit on its own UART: command, wait, read, decode."""

    __slots__ = ("link", "sleep")

    def __init__(self, link: UartLink, sleep: bool = True) -> None:
        self.link = link
        self.sleep = sleep
        """Whether to wait the datasheet's 800ms reading time; off against a fake."""

    def read(self) -> float:
        """The current ORP in mV: one `R` command, one reading frame (after any `*OK`)."""
        frame = read_frame(self.link, self.sleep, READ_DELAY_S)
        return parse_orp(frame)


class EzoOrp(Readable):
    """One EZO-ORP probe on the device root: `orp [RP]`, one UART round trip."""

    orp = Output("orp", quantity=ORP, range=(-1020.0, 1020.0), precision=1)

    def __init__(
        self,
        name: str,
        link: UartLink,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.probe = EzoOrpProbe(link, sleep)

    @property
    def config(self) -> EzoOrpConfig:
        return EzoOrpConfig(link="")

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, orp=self.probe.read())


class EzoOrpConfig(DriverConfig[EzoOrp], tag="ezo_orp"):
    """One EZO-ORP circuit, alone on its UART."""

    link: UartLinkConfig | str  # type: ignore[valid-type]

    def build(self, name: str, label: str | None = None) -> EzoOrp:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return EzoOrp(name, resolve(self.link), label=label)


EzoOrp.config_type = EzoOrpConfig  # the config is declared after the device it builds


__all__ = [
    "ORP",
    "READ_DELAY_S",
    "EzoOrp",
    "EzoOrpConfig",
    "EzoOrpProbe",
    "parse_orp",
]
