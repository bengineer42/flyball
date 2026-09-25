r"""Shared wire-protocol machinery for Atlas Scientific EZO-family chips, over UART or I2C.

Not a driver itself: no `DriverConfig`, no type. Every EZO circuit in this package
(`ezo_ph`, `ezo_ec`, `ezo_orp`, `ezo_do`) shares two transports and a decode boundary
between them.

**UART** (38400 8N1, `\\r`-terminated ASCII): send a command, wait the chip's own
processing time, read a reply frame, and -- because the circuit's `*OK` acknowledgement
response code defaults to *enabled* -- read a second frame if the first one was `*OK\\r`
rather than data. Any other `*`-prefixed frame (`*ER`, `*OV`, `*UV`, ...) is a protocol
error, not a reading. A command that only acknowledges (a calibration point, `T,<value>`)
gets no second frame: `*OK\\r` is the whole reply, and anything else is a failure.

**I2C** (Atlas Scientific pH EZO datasheet v1.3, pp.29-31, "I2C mode" / "Data from a read
back event" / "I2C timing"): a write event is the bare ASCII command, no `\\r`; a read
event returns a status byte (1 success, 2 syntax error, 254 still processing -- the wait
before reading back was too short, 255 no data pending) followed by a NULL-terminated ASCII
string, not a `\\r`-terminated one. EZO circuits do not support I2C clock stretching, so the
caller must wait the *specific command's* processing delay -- it is not fixed the way UART's
per-chip `READ_DELAY_S` is; each cal/compensation command below carries its own. An I2C
reply's ASCII text is re-terminated with `\\r` here (`_i2c_reply`), so it reads through the
same `decode_text`/`parse_*` functions as a UART one, unmodified.

What is NOT here: conversion-time constants (they differ per chip and per command; see each
chip module), the reply's decode formula, config fields and tags, calibration command
strings and temperature-compensation wiring -- those stay in each chip module.
"""

from __future__ import annotations

import time

from flyball.foundation.device import InputBinding, values_of
from flyball.foundation.errors import HardwareError
from flyball.hardware.i2c import I2cLink
from flyball.hardware.uart import UartLink

READ_COMMAND = b"R"
"""'Returns a single reading' -- identical across the EZO family's datasheets."""

OK_FRAME = b"*OK\r"
"""The (default-enabled) command-acknowledged response code; not a reading."""

I2C_STATUS_SUCCESS = 1
I2C_STATUS_SYNTAX_ERROR = 2
I2C_STATUS_STILL_PROCESSING = 254
I2C_STATUS_NO_DATA = 255
"""The I2C reply's first byte (pH datasheet p.30, "Data from a read back event")."""

I2C_REPLY_LENGTH = 32
"""Bytes requested per I2C read-back: the longest reply (EZO-EC's 4-field CSV) plus its NULL."""


def read_frame(link: UartLink, sleep: bool, delay_s: float, command: bytes = READ_COMMAND) -> bytes:
    r"""One UART command, one reply frame.

    Waits the datasheet's processing time, then reads one frame and, if it is
    the `*OK` acknowledgement, reads a second frame for the actual value. Does
    not assume the response code has been disabled. `command` (bare ASCII, no
    `\\r`) defaults to `R`; a calibration query (`cal,?`, `T,?`) passes its own.
    """
    link.write(command + b"\r")
    if sleep:
        time.sleep(delay_s)
    frame = link.read_until(b"\r")
    if frame == OK_FRAME:
        frame = link.read_until(b"\r")
    return frame


def ack_frame(link: UartLink, command: bytes, sleep: bool, delay_s: float, chip: str) -> None:
    """One UART command that only acknowledges (a calibration point, `T,<value>`, ...).

    Raises:
        HardwareError: The reply was not `*OK` -- `*ER`, or anything else.
    """
    link.write(command + b"\r")
    if sleep:
        time.sleep(delay_s)
    frame = link.read_until(b"\r")
    if frame != OK_FRAME:
        text = frame.decode("ascii", errors="replace").strip().rstrip("\r")
        raise HardwareError(f"{chip}: {command.decode('ascii')!r} not acknowledged: {text!r}")


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


def _i2c_reply(frame: bytes, command: bytes, chip: str) -> bytes:
    r"""The I2C reply's ASCII text, re-terminated with `\\r` like a UART frame.

    Checks the status byte (pH datasheet p.30) and strips the NULL terminator (p.31)
    rather than the `\\r` a UART reply carries, so the result reads through the same
    `decode_text`/`parse_*` functions unmodified.

    Raises:
        HardwareError: An empty reply, or a status byte other than success.
    """
    if not frame:
        raise HardwareError(f"{chip}: empty I2C reply to {command!r}")
    status = frame[0]
    if status == I2C_STATUS_SYNTAX_ERROR:
        raise HardwareError(f"{chip}: I2C syntax error (status 2) replying to {command!r}")
    if status == I2C_STATUS_STILL_PROCESSING:
        raise HardwareError(
            f"{chip}: I2C reply to {command!r} still processing (status 254) -- the wait "
            "before reading back was too short"
        )
    if status == I2C_STATUS_NO_DATA:
        raise HardwareError(f"{chip}: I2C has no data to send (status 255) for {command!r}")
    if status != I2C_STATUS_SUCCESS:
        raise HardwareError(f"{chip}: unexpected I2C status byte {status} replying to {command!r}")
    return frame[1:].split(b"\x00", 1)[0] + b"\r"


def i2c_exchange(
    link: I2cLink, i2c_address: int, command: bytes, sleep: bool, delay_s: float, chip: str
) -> bytes:
    r"""One ASCII command over I2C: write (no `\\r`), wait `delay_s`, read the reply back.

    `delay_s` is the *specific command's* processing delay (pH datasheet p.31, "I2C
    timing": "Each command specifies the delay needed... EZO class devices do not
    support I2C clock stretching"), unlike UART where only `R` differs per chip.
    """
    link.write(i2c_address, command)
    if sleep:
        time.sleep(delay_s)
    return _i2c_reply(link.read(i2c_address, I2C_REPLY_LENGTH), command, chip)


class EzoTransport:
    r"""One EZO circuit's wire access, over UART or I2C -- chosen by what `link` is.

    `i2c_address` is used only over I2C. `read`/`write` both take bare ASCII commands (no
    `\\r`) and return the `\\r`-terminated reply frame each chip's `decode_text`/`parse_*`
    already expects, whichever transport it came over -- so a chip module writes its
    command strings once and does not branch on transport itself.
    """

    __slots__ = ("chip", "i2c_address", "link", "sleep")

    def __init__(
        self, link: UartLink | I2cLink, chip: str, i2c_address: int = 0, sleep: bool = True
    ) -> None:
        self.link = link
        self.chip = chip
        self.i2c_address = i2c_address
        self.sleep = sleep
        """Whether to wait each command's processing delay; off against a fake."""

    @property
    def is_i2c(self) -> bool:
        """Whether this circuit is wired for I2C rather than UART."""
        return isinstance(self.link, I2cLink)

    def read(self, command: bytes, delay_s: float) -> bytes:
        """A command that answers with data (`R`, `cal,?`, `T,?`): the reply frame."""
        link = self.link
        if isinstance(link, I2cLink):
            return i2c_exchange(link, self.i2c_address, command, self.sleep, delay_s, self.chip)
        return read_frame(link, self.sleep, delay_s, command=command)

    def write(self, command: bytes, delay_s: float) -> None:
        """A command that only acknowledges (a calibration point, `T,<value>`, ...)."""
        link = self.link
        if isinstance(link, I2cLink):
            i2c_exchange(link, self.i2c_address, command, self.sleep, delay_s, self.chip)
        else:
            ack_frame(link, command, self.sleep, delay_s, self.chip)


__all__ = [
    "I2C_REPLY_LENGTH",
    "I2C_STATUS_NO_DATA",
    "I2C_STATUS_STILL_PROCESSING",
    "I2C_STATUS_SUCCESS",
    "I2C_STATUS_SYNTAX_ERROR",
    "OK_FRAME",
    "READ_COMMAND",
    "EzoTransport",
    "ack_frame",
    "decode_text",
    "i2c_exchange",
    "read_frame",
]


def compensation(*inputs: InputBinding) -> dict[str, float]:
    """The bound compensation inputs' values, by input name; an unbound one is left out.

    An optional input left out of the rig file is unbound and means "no compensation".
    A bound one is used exactly as a required input is: it never quietly falls back to
    reading uncompensated.

    Raises:
        NotReadyError: A bound input has nothing yet (`pending`): omit this read.
        NoValueError: What a bound input follows has no value: push its `no_value` on the
            outputs, so the reading carries the input's quality.
    """
    bound = [binding for binding in inputs if binding.bound]
    if not bound:
        return {}
    return {b.name: float(v) for b, v in zip(bound, values_of(*bound), strict=True)}
