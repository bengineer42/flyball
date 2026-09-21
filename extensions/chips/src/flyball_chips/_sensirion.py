"""Shared wire-protocol machinery for Sensirion-style command/response chips.

Not a driver itself: no `DriverConfig`, no tag. Every Sensirion-family chip
in this package (`sht4x`, `sht31`, `scd30`, `scd4x`, `sgp30`, `sgp40`) sends a
command, waits a datasheet conversion time, and reads back a reply made of
16-bit big-endian words each followed by a CRC-8 byte (polynomial 0x31).
`htu21d` uses the same polynomial but a different CRC initial value (0x00,
not Sensirion's 0xFF) -- `crc8` takes `init` so both fit the one function.

What is NOT here: command bytes, conversion-time waits, decode formulas,
config fields and tags -- those differ per chip and stay in each module.
"""

from __future__ import annotations

from flyball.foundation.errors import HardwareError

SENSIRION_CRC_INIT = 0xFF
"""The initial CRC value Sensirion's own chips use; HTU21D/Si7021 use 0x00 instead."""


def crc8(data: bytes, init: int = SENSIRION_CRC_INIT) -> int:
    """Polynomial 0x31 (x^8+x^5+x^4+1) CRC-8, initial value `init`, no final XOR.

    Sensirion's own chips (SHT3x/4x, SCD30/4x, SGP30/40) all use `init=0xFF`.
    HTU21D/Si7021 share the polynomial but start from `init=0x00`.
    """
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc_words(frame: bytes, count: int, init: int = SENSIRION_CRC_INIT) -> list[int]:
    """`count` big-endian 16-bit words from `frame`, each followed by its own CRC-8.

    Raises:
        HardwareError: A short frame, or a CRC that does not match.
    """
    if len(frame) != count * 3:
        raise HardwareError(f"expected {count * 3} bytes of CRC-protected words, got {len(frame)}")
    words = []
    for i in range(count):
        word = frame[3 * i : 3 * i + 2]
        crc = frame[3 * i + 2]
        if crc8(word, init) != crc:
            raise HardwareError(f"CRC mismatch in word {i} of {frame.hex()}")
        words.append(int.from_bytes(word, "big"))
    return words


def word_with_crc(value: int, init: int = SENSIRION_CRC_INIT) -> list[int]:
    """A 16-bit value as its two bytes plus the CRC-8 of those two bytes."""
    data = [(value >> 8) & 0xFF, value & 0xFF]
    return [*data, crc8(bytes(data), init)]


def command(code: int) -> list[int]:
    """The two command bytes of a 16-bit command word, MSB first."""
    return [code >> 8, code & 0xFF]


__all__ = [
    "SENSIRION_CRC_INIT",
    "command",
    "crc8",
    "crc_words",
    "word_with_crc",
]
