r"""Shared wire-protocol machinery for Atlas Scientific EZO-family chips over UART.

Not a driver itself: no `DriverConfig`, no tag. Every EZO circuit in this package
(`ezo_ph`, `ezo_ec`, `ezo_orp`, `ezo_do`) shares one UART framing (38400 8N1,
`\\r`-terminated ASCII) and one single-reading exchange: send `R\\r`, wait the
chip's own conversion time, read a reply frame, and -- because the circuit's
`*OK` acknowledgement response code defaults to *enabled* -- read a second frame
if the first one was `*OK\\r` rather than a reading. Any other `*`-prefixed frame
(`*ER`, `*OV`, `*UV`, ...) is a protocol error, not a reading.

What is NOT here: the conversion-time constant (it differs per chip: pH 1000ms,
EC/DO 600ms, ORP 800ms per their datasheets), the reply's decode formula, config
fields and tags -- those stay in each chip module.
"""

from __future__ import annotations

import time

from flyball.core.errors import HardwareError
from flyball.hardware.uart import UartLink

READ_COMMAND = b"R\r"
"""'Returns a single reading' -- identical across the EZO family's datasheets."""

OK_FRAME = b"*OK\r"
"""The (default-enabled) command-acknowledged response code; not a reading."""


def read_frame(link: UartLink, sleep: bool, delay_s: float) -> bytes:
    """One `R` command, one reply frame.

    Waits the datasheet's conversion time, then reads one frame and, if it is
    the `*OK` acknowledgement, reads a second frame for the actual value. Does
    not assume the response code has been disabled.
    """
    link.write(READ_COMMAND)
    if sleep:
        time.sleep(delay_s)
    frame = link.read_until(b"\r")
    if frame == OK_FRAME:
        frame = link.read_until(b"\r")
    return frame


def decode_text(frame: bytes, chip: str) -> str:
    """`frame` as stripped ASCII text, after raising on a `*`-prefixed status frame.

    Raises:
        HardwareError: A `*`-prefixed status/error frame (`*ER`, `*OV`, `*UV`,
            ...) in place of a reading.
    """
    text = frame.decode("ascii", errors="replace").strip().rstrip("\r")
    if text.startswith("*"):
        raise HardwareError(f"{chip} status frame instead of a reading: {text!r}")
    return text


__all__ = [
    "OK_FRAME",
    "READ_COMMAND",
    "decode_text",
    "read_frame",
]
