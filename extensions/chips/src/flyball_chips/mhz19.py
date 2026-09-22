"""Winsen MH-Z19(B): CO2 over UART, a fixed 9-byte binary frame each way.

Per Winsen's public MH-Z19B datasheet/protocol note: 9600 8N1, request and
reply are both 9 bytes starting `0xFF 0x01`, ending in a checksum
(`0xFF - (sum of bytes 1..7) + 1`, i.e. two's complement of that sum). The
"read CO2 concentration" command is `0x86`; the reply's CO2 ppm is
`byte[2] * 256 + byte[3]`. Decoded from the datasheet only -- never run
against a real sensor.
"""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.config import resolve
from flyball.foundation.device import DriverConfig, Node, Output, Readable, Sample
from flyball.foundation.errors import HardwareError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import PartsPerMillion
from flyball.hardware.uart import UartLink

from flyball_chips._links import UartLinkConfig

CO2 = Quantity("co2", PartsPerMillion)

READ_CO2 = bytes([0xFF, 0x01, 0x86, 0x00, 0x00, 0x00, 0x00, 0x00])
"""The 8 bytes before the checksum; `request()` appends it."""


def checksum(frame: bytes) -> int:
    """Winsen's checksum over `frame[1:8]`: two's complement of the sum, masked to a byte."""
    return (0xFF - (sum(frame[1:8]) & 0xFF) + 1) & 0xFF


def request(command: bytes = READ_CO2) -> bytes:
    """The 9-byte frame to send, checksum appended."""
    return command + bytes([checksum(command)])


def decode(frame: bytes) -> int:
    """CO2 in ppm from a 9-byte reply.

    Raises:
        HardwareError: The frame is the wrong length, doesn't start `0xFF 0x86`,
            or its checksum doesn't match.
    """
    if len(frame) != 9:
        raise HardwareError(f"MH-Z19 reply is {len(frame)} bytes, not 9")
    if frame[0] != 0xFF or frame[1] != 0x86:
        raise HardwareError(f"MH-Z19 reply does not start FF 86: {frame.hex()}")
    if checksum(frame) != frame[8]:
        raise HardwareError(f"MH-Z19 checksum mismatch in {frame.hex()}")
    return frame[2] * 256 + frame[3]


def encode(ppm: int) -> bytes:
    """The frame the sensor would send for `ppm`; for fakes and tests."""
    frame = bytearray([0xFF, 0x86, (ppm >> 8) & 0xFF, ppm & 0xFF, 0, 0, 0, 0])
    frame.append(checksum(bytes(frame)))
    return bytes(frame)


class MhZ19Sensor:
    """One sensor on a port: write the request, read the 9-byte reply, decode."""

    __slots__ = ("link",)

    def __init__(self, link: UartLink) -> None:
        self.link = link

    def read(self) -> int:
        """CO2 in ppm: one request/reply round trip."""
        self.link.write(request())
        return decode(self.link.read(9))


class MhZ19(Readable):
    """One MH-Z19(B) on its own UART port: `co2 [RP]`, one request/reply per read."""

    co2 = Output("co2", quantity=CO2, range=(0.0, 5000.0))

    def __init__(self, name: str, link: UartLink, label: str | None = None) -> None:
        super().__init__(name, label)
        self.link = link
        self.sensor = MhZ19Sensor(link)

    @property
    def config(self) -> MhZ19Config:
        return MhZ19Config(link="")

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield self.sample(time_ns, co2=self.sensor.read())


class MhZ19Config(DriverConfig[MhZ19], tag="mhz19"):
    """One MH-Z19(B) on its own UART port."""

    link: UartLinkConfig | str  # type: ignore[valid-type]

    def build(self, name: str, label: str | None = None) -> MhZ19:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a port before building")
        return MhZ19(name, resolve(self.link), label=label)


MhZ19.config_type = MhZ19Config
