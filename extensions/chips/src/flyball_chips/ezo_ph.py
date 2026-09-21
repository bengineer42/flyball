r"""Atlas Scientific EZO-pH: one probe, one ASCII command/response cycle over UART.

Built from Atlas Scientific's published protocol documentation ("pH Circuit EZO",
datasheet v1.3: https://cdn.sparkfun.com/datasheets/Sensors/Biometric/pH_EZO_datasheet_v13.pdf),
not from a real device: this driver has never been run against a physical EZO-pH
circuit or probe.

Protocol, per the datasheet: default UART is 38400 8N1, no flow control. Commands
are ASCII strings terminated by a carriage return (`\\r`, decimal 13); a single
reading is `R\\r`, answered `pH<CR>` (e.g. `"7.002\\r"`) one second later ("Single
reading mode", p.16). The circuit's seven response codes share the `*` prefix and
a `<CR>` terminator (`*OK`, `*ER`, `*OV`, `*UV`, `*RS`, `*RE`, `*SL`, `*WA`, p.21);
only `*OK` can be disabled (`RESPONSE,0\\r`), and it defaults to *enabled*, so a
factory-default circuit answers `R\\r` with `*OK<CR>` followed by the `pH<CR>`
frame. This driver does not assume the response code has been turned off: it reads
one frame, and if that frame is the `*OK` acknowledgement, reads a second frame for
the actual value. Any other `*`-prefixed frame (`*ER`, `*OV`, `*UV`, ...) is a
protocol error, not a reading, and raises.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.errors import HardwareError
from flyball.core.quantity import Quantity
from flyball.core.signal import Node, Sample
from flyball.core.units.dimensions import Fraction
from flyball.hardware.uart import UartLink

from flyball_chips._links import UartLinkConfig

READ_COMMAND = b"R\r"
"""'Returns a single reading' (datasheet p.16)."""

READ_DELAY_S = 1.0
"""The datasheet's single-reading mode: the pH frame follows one second later."""

OK_FRAME = b"*OK\r"
"""The (default-enabled) command-acknowledged response code; not a reading."""

pH = Fraction.unit("pH", "pH", 1.0, scale=(0.0, 14.0))
PH = Quantity("pH", pH)


def parse_ph(frame: bytes) -> float:
    """The pH value from a `pH<CR>` reply frame.

    Raises:
        HardwareError: A `*`-prefixed status/error frame (`*ER`, `*OV`, `*UV`, ...)
            in place of a reading, or a frame that is not a decimal number.
    """
    text = frame.decode("ascii", errors="replace").strip().rstrip("\r")
    if text.startswith("*"):
        raise HardwareError(f"EZO-pH status frame instead of a reading: {text!r}")
    try:
        return float(text)
    except ValueError as exc:
        raise HardwareError(f"EZO-pH reply {frame!r} is not a decimal pH value") from exc


class EzoPhProbe:
    """One EZO-pH circuit on its own UART: command, wait, read, decode."""

    __slots__ = ("link", "sleep")

    def __init__(self, link: UartLink, sleep: bool = True) -> None:
        self.link = link
        self.sleep = sleep
        """Whether to wait the datasheet's one-second reading time; off against a fake."""

    def read(self) -> float:
        """The current pH: one `R` command, one reading frame (after any `*OK`)."""
        self.link.write(READ_COMMAND)
        if self.sleep:
            time.sleep(READ_DELAY_S)
        frame = self.link.read_until(b"\r")
        if frame == OK_FRAME:
            frame = self.link.read_until(b"\r")
        return parse_ph(frame)


class EzoPh(Readable):
    """One EZO-pH probe on the device root: `ph [RP]`, one UART round trip."""

    ph = Output("ph", quantity=PH, range=(0.0, 14.0), precision=3)

    def __init__(
        self,
        name: str,
        link: UartLink,
        sleep: bool = True,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.probe = EzoPhProbe(link, sleep)

    @property
    def config(self) -> EzoPhConfig:
        return EzoPhConfig(link="")

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, ph=self.probe.read())


class EzoPhConfig(DriverConfig[EzoPh], tag="ezo_ph"):
    """One EZO-pH circuit, alone on its UART."""

    link: UartLinkConfig | str  # type: ignore[valid-type]

    def build(self, name: str, label: str | None = None) -> EzoPh:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return EzoPh(name, resolve(self.link), label=label)


EzoPh.config_type = EzoPhConfig  # the config is declared after the device it builds


__all__ = [
    "OK_FRAME",
    "PH",
    "READ_COMMAND",
    "READ_DELAY_S",
    "EzoPh",
    "EzoPhConfig",
    "EzoPhProbe",
    "pH",
    "parse_ph",
]
