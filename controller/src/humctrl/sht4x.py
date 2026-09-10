from __future__ import annotations

import struct
import time
from collections.abc import Generator, Iterable
from contextlib import contextmanager, suppress
from threading import RLock
from typing import overload

from humctrl.i2c import I2CBus
from humctrl.readers import HTReading

_TRIGGER = 0xFD  # Mode.NOHEAT_HIGHPRECISION
_CONVERSION_NS = 8_500_000  # datasheet t_meas max 8.3 ms, plus margin
_MUX_ADDR = 0x70
_SHT4X_ADDR = 0x44


class CrcError(Exception):
    """Raised when a sensor read fails due to CRC mismatch."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SHT4xTriggerError(Exception):
    """Raised when a sensor trigger fails."""

    def __init__(self, sensor: str, cause: OSError) -> None:
        self.sensor = sensor
        self.cause = cause
        super().__init__(f"Trigger failed for sensor {sensor!r}: {cause}")


class SHT4xReadError(Exception):
    """Raised when a sensor read fails."""

    def __init__(self, sensor: str, cause: OSError | CrcError) -> None:
        self.sensor = sensor
        self.cause = cause
        super().__init__(f"Read failed for sensor {sensor!r}: {cause}")


def _crc8(data: bytes) -> int:
    """Sensirion CRC-8: poly 0x31, init 0xFF."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _decode(buf: bytes) -> tuple[Percent, float]:
    if _crc8(buf[0:2]) != buf[2] or _crc8(buf[3:5]) != buf[5]:
        raise CrcError(buf.hex())
    raw_t = struct.unpack_from(">H", buf, 0)[0]
    raw_h = struct.unpack_from(">H", buf, 3)[0]
    return (
        min(100.0, max(0.0, -6.0 + 125.0 * raw_h / 65535.0)),
        -45.0 + 175.0 * raw_t / 65535.0,
    )


class MuxedSHT4xBank:
    def __init__(
        self,
        i2c: I2CBus,
        channels: dict[str, int],
        mux_address: int = _MUX_ADDR,
        sensor_address: int = _SHT4X_ADDR,
    ) -> None:
        self._i2c = i2c
        self._channels = channels
        self._mux = mux_address
        self._addr = sensor_address
        self._lock = RLock()
        self._buf = bytearray(6)

    def _select(self, mask: int) -> None:
        self._i2c.writeto(self._mux, bytes((mask,)))

    @overload
    def read(self, time_ns: int, sensors: str) -> HTReading | Exception | None: ...
    @overload
    def read(
        self, time_ns: int, sensors: Iterable[str] | None = None
    ) -> dict[str, HTReading | Exception]: ...
    def read(
        self, time_ns: int, sensors: str | Iterable[str] | None = None
    ) -> HTReading | Exception | dict[str, HTReading | Exception] | None:
        if isinstance(sensors, str):
            return self.read_sensor(time_ns, sensors)
        if sensors is None:
            return self.read_all(time_ns)
        return self.read_sensors(time_ns, sensors)

    def read_sensor(self, time_ns: int, sensor: str) -> HTReading | Exception | None:
        offset_ns = time_ns - time.monotonic_ns()
        if (channel := self._channels.get(sensor)) is None:
            return None
        with self.lock():
            try:
                r_ns = self._trigger_channel(channel)
            except OSError as e:
                return SHT4xTriggerError(sensor, e)
            return self._try_read(sensor, channel, offset_ns, r_ns)

    def read_sensors(
        self, time_ns: int, sensors: Iterable[str]
    ) -> dict[str, HTReading | Exception]:
        offset_ns = time_ns - time.monotonic_ns()
        results: dict[str, HTReading | Exception] = {}
        triggered: list[tuple[str, int, int]] = []

        with self.lock():
            for label in sensors:
                channel = self._channels.get(label)
                if channel is not None:
                    try:
                        triggered.append((label, channel, self._trigger_channel(channel)))
                    except OSError as e:
                        results[label] = SHT4xTriggerError(label, e)

            for label, channel, r_ns in triggered:
                results[label] = self._try_read(label, channel, offset_ns, r_ns)

        return results

    def _trigger_channel(self, channel: int) -> int:
        self._select(1 << channel)
        self._i2c.writeto(self._addr, bytes((_TRIGGER,)))
        return time.monotonic_ns()

    def _try_read(self, label: str, channel: int, wall_ns: int, r_ns: int) -> HTReading | Exception:
        try:
            return HTReading(wall_ns + r_ns, *self._read_channel(channel, r_ns), label)
        except (OSError, CrcError) as e:
            return SHT4xReadError(label, e)

    def _read_channel(self, channel: int, time_ns: int) -> tuple[Percent, float]:
        remaining = time_ns + _CONVERSION_NS - time.monotonic_ns()
        if remaining > 0:
            time.sleep(remaining / 1e9)
        self._select(1 << channel)
        self._i2c.readfrom_into(self._addr, self._buf)
        return _decode(bytes(self._buf))

    @contextmanager
    def lock(self) -> Generator[None, None, None]:
        """Context manager to lock the bank for multiple reads."""
        with self._lock:
            self._i2c.try_lock()
            try:
                yield
            finally:
                with suppress(OSError):
                    self._select(0x00)
                self._i2c.unlock()

    def read_all(self, time_ns: int) -> dict[str, HTReading | Exception]:
        return self.read_sensors(time_ns, self._channels.keys())


class SHT4x:
    """A single SHT4x sensor on an I2C bus."""

    def __init__(self, i2c: I2CBus, label: str, address: int = _SHT4X_ADDR) -> None:
        self._i2c = i2c
        self._addr = address
        self._label = label
        self._lock = RLock()
        self._buf = bytearray(6)

    def read(self, time_ns: int) -> HTReading:
        """Trigger a conversion, wait it out, and read the result.

        The reading is stamped at the instant the conversion was triggered,
        expressed in the caller's epoch. Raises ``OSError`` on a bus fault and
        ``CrcError`` on a corrupt frame.
        """
        offset_ns = time_ns - time.monotonic_ns()
        with self._lock:
            self._i2c.try_lock()
            try:
                self._i2c.writeto(self._addr, bytes((_TRIGGER,)))
                trigger_ns = time.monotonic_ns()
                time.sleep(_CONVERSION_NS / 1e9)
                self._i2c.readfrom_into(self._addr, self._buf)
                humidity, temperature = _decode(bytes(self._buf))
            finally:
                self._i2c.unlock()
        return HTReading(trigger_ns + offset_ns, humidity, temperature, self._label)
