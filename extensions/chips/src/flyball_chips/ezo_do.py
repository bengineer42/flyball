r"""Atlas Scientific EZO-DO: one probe, one ASCII command/response cycle over UART.

Built from Atlas Scientific's published protocol documentation ("EZO-DO Embedded
Dissolved Oxygen Circuit", datasheet v5.8: https://files.atlas-scientific.com/DO_EZO_Datasheet.pdf),
not from a real device: this driver has never been run against a physical EZO-DO
circuit or probe.

Protocol, per the datasheet: default UART is 38400 8N1, no flow control (p.10-11,
"Default state"/"UART mode"). Commands are ASCII strings terminated by a carriage
return (`\\r`, decimal 13); a single reading is `R\\r`, answered 600ms later
("Single reading mode", p.19). The circuit's `O` command (p.28) enables or
disables which parameters the `R` reply carries -- dissolved oxygen in mg/L, and
optionally percent saturation appended as a second, comma-separated field -- and
its factory default is `mg/L` alone (the UART quick-reference table, p.15,
lists `O`'s default state as `mg/L`). This driver decodes exactly that default:
a single mg/L reading, 2 decimal places (p.11's "Data format" table), e.g.
`"7.82\r"`. It does not decode the CSV `mg/L,%sat` form the `O` command can turn
on -- that is a rig-file misconfiguration relative to this driver's assumption,
not something it guesses at. The circuit's response codes share the `*` prefix
and a `<CR>` terminator; only `*OK` can be disabled, and it defaults to
*enabled*, so a factory-default circuit answers `R\r` with `*OK<CR>` followed by
the reading frame. This driver does not assume the response code has been
turned off: it reads one frame, and if that frame is the `*OK` acknowledgement,
reads a second frame for the actual value. The datasheet gives the D.O. range
as 0.00-100 mg/L (p.1).
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Readable, Readout, Sample
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity, Unit
from flyball.hardware.uart import UartLink

from flyball_chips._links import UartLinkConfig

from ._ezo import decode_text, read_frame

READ_DELAY_S = 0.6
"""The datasheet's single-reading mode: the D.O. frame follows 600ms later (p.19)."""

DISSOLVED_OXYGEN = Quantity("dissolved_oxygen", Unit.get("mg/L"))


def parse_do(frame: bytes) -> float:
    """The dissolved-oxygen value, in mg/L, from a reply frame.

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
    """One EZO-DO circuit on its own UART: command, wait, read, decode."""

    __slots__ = ("link", "sleep")

    def __init__(self, link: UartLink, sleep: bool = True) -> None:
        self.link = link
        self.sleep = sleep
        """Whether to wait the datasheet's 600ms reading time; off against a fake."""

    def read(self) -> float:
        """The current D.O. in mg/L: one `R` command, one reading frame (after any `*OK`)."""
        frame = read_frame(self.link, self.sleep, READ_DELAY_S)
        return parse_do(frame)


class EzoDo(Readable):
    """One EZO-DO probe on the device root: `dissolved_oxygen [RP]`, one UART round trip."""

    dissolved_oxygen = Readout(
        "dissolved_oxygen", quantity=DISSOLVED_OXYGEN, range=(0.0, 100.0), precision=2
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
        self.probe = EzoDoProbe(link, sleep)

    @property
    def config(self) -> EzoDoConfig:
        return EzoDoConfig(link="")

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, dissolved_oxygen=self.probe.read())


class EzoDoConfig(DriverConfig[EzoDo], tag="ezo_do"):
    """One EZO-DO circuit, alone on its UART."""

    link: UartLinkConfig | str  # type: ignore[valid-type]

    def build(self, name: str, label: str | None = None) -> EzoDo:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a bus before building")
        return EzoDo(name, resolve(self.link), label=label)


EzoDo.config_type = EzoDoConfig  # the config is declared after the device it builds


__all__ = [
    "DISSOLVED_OXYGEN",
    "READ_DELAY_S",
    "EzoDo",
    "EzoDoConfig",
    "EzoDoProbe",
    "parse_do",
]
