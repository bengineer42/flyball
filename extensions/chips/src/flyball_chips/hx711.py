"""Avia Semiconductor HX711: 24-bit ADC for a load cell, bit-banged over two raw GPIO lines.

Not a bus: no address, no chip-select, no registers. The host toggles a
clock line (`PD_SCK`) and the chip shifts one bit of its last conversion out
on a data line (`DOUT`) per rising edge, MSB first, 24 bits, two's
complement. `DOUT` idles high and drops low when a conversion is ready --
that is the "data ready" the host polls or waits on before clocking.

The number of clock pulses *beyond* the 24 data bits -- 25, 26 or 27 in
total -- selects the channel and gain the chip will use for its *next*
conversion (datasheet Table 3, confirmed against the Avia Semiconductor
HX711 datasheet, "Serial Interface" and Fig. 2):

| total pulses | channel | gain |
| --- | --- | --- |
| 25 | A | 128 |
| 26 | B | 32 |
| 27 | A | 64 |

Reading always requests the *same* channel/gain again (25 pulses for A128,
etc.), since a bare read has nowhere to say "and now switch" -- a caller
that wants to alternate channels reads with a different `gain` each time,
which takes effect on the conversion *after* the one just read.

## Timing, honestly

The datasheet (Fig. 2, Table under it) gives `PD_SCK` high time (T3) as
0.2-50µs and low time (T4) as at least 0.2µs, and pin `PD_SCK` held high
for more than 60µs (Fig. 3) puts the whole chip into power-down -- which
would corrupt whatever read was in progress and silently drop the chip's
channel/gain state back to its A/128 default.

A CPython call to `gpiod`'s `set_value`/`get_value` crosses into the
kernel; on a normal Linux host that is typically low single-digit
microseconds, but it is not bounded -- the GIL, the scheduler, or a page
fault can stall a call for far longer than 50µs with no warning. This
driver does not attempt to hit T3/T4 with a busy-wait or `sleep()` (the
latter has millisecond-scale granularity on most systems, which would
blow the 50µs ceiling on every single pulse) -- it just does the two
syscalls back to back and trusts they are fast, which is a real, unquantified
risk of occasional power-down mid-read or a corrupted bit. If this happens
in practice, `read_raw()`'s 24-bit read will look fine (garbage still
decodes as *some* 24-bit number) -- so a bad read is not distinguishable
from a good one by shape alone; only downstream sanity limits on the
decoded weight would catch it. A real deployment on a busy Linux host
should budget for this, e.g. with a kernel PWM/SPI-shim front end, a
microcontroller doing the bit-banging, or simply averaging several
reads and rejecting outliers. This driver has been decoded from the
datasheet and never run against real hardware.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Literal

from flyball.core.config import resolve
from flyball.core.device import DriverConfig, Output, Readable
from flyball.core.errors import HardwareError
from flyball.core.quantity import Quantity
from flyball.core.signal import Node, Sample
from flyball.core.units.si import Gram
from flyball.hardware.gpio import GpioLink
from pydantic import Field

from flyball_chips._links import GpioLinkConfig

Gain = Literal["a128", "b32", "a64"]
GAIN_PULSES: dict[Gain, int] = {"a128": 25, "b32": 26, "a64": 27}
"""Total PD_SCK pulses for one read at this channel/gain (datasheet Table 3)."""

WEIGHT = Quantity("weight", Gram)


def decode(raw: int) -> int:
    """24-bit two's complement -> a signed int, `-0x800000..0x7FFFFF`."""
    if raw & 0x800000:
        return raw - (1 << 24)
    return raw


class Hx711Sensor:
    """One chip: clock out on `clock_line`, data in on `data_line`, both on `link`.

    `pulse_s`, when set, sleeps that long after each clock edge -- useful
    only for a fake in a test, since real `PD_SCK` timing (T3 max 50us) is
    far tighter than `time.sleep`'s granularity; see the module docstring.
    `0` (the default) does no sleep at all and just trusts the two GPIO
    calls per pulse to be fast enough, which is the honest posture for real
    hardware.
    """

    __slots__ = ("clock_line", "data_line", "gain", "link", "pulse_s")

    def __init__(
        self,
        link: GpioLink,
        clock_line: int,
        data_line: int,
        gain: Gain = "a128",
        pulse_s: float = 0.0,
    ) -> None:
        self.link = link
        self.clock_line = clock_line
        self.data_line = data_line
        self.gain: Gain = gain
        self.pulse_s = pulse_s
        link.claim_output(clock_line, initial=False)
        link.claim_input(data_line)

    def ready(self) -> bool:
        """`DOUT` low: a conversion is waiting to be clocked out."""
        return not self.link.get(self.data_line)

    def _pulse(self) -> bool:
        """One clock cycle: high, sample `DOUT`, low. Returns the sampled bit."""
        self.link.set(self.clock_line, True)
        if self.pulse_s:
            time.sleep(self.pulse_s)
        bit = self.link.get(self.data_line)
        self.link.set(self.clock_line, False)
        if self.pulse_s:
            time.sleep(self.pulse_s)
        return bit

    def read_raw(self) -> int:
        """The signed 24-bit conversion, clocked out at `self.gain`.

        Raises:
            HardwareError: `DOUT` is not low -- no conversion is ready.
        """
        if not self.ready():
            raise HardwareError("HX711 DOUT is high: no conversion ready")
        value = 0
        for _ in range(24):
            value = (value << 1) | int(self._pulse())
        for _ in range(GAIN_PULSES[self.gain] - 24):
            self._pulse()
        return decode(value)


class Hx711(Readable):
    """One load cell: `weight [RP]`, `weight = raw * scale + offset`.

    `scale`/`offset` turn raw ADC counts into a real unit -- inherently
    per-load-cell, found by taring at zero load and again at a known
    reference weight (mirrors `Channel.scale`/`offset` in `ads1115.py`).
    """

    weight = Output("weight", quantity=WEIGHT)

    def __init__(
        self,
        name: str,
        link: GpioLink,
        clock_line: int,
        data_line: int,
        gain: Gain = "a128",
        scale: float = 1.0,
        offset: float = 0.0,
        pulse_s: float = 0.0,
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        self.link = link
        self.scale = scale
        self.offset = offset
        self.sensor = Hx711Sensor(link, clock_line, data_line, gain, pulse_s)

    @property
    def config(self) -> Hx711Config:
        return Hx711Config(
            link="",
            clock_line=self.sensor.clock_line,
            data_line=self.sensor.data_line,
            gain=self.sensor.gain,
            scale=self.scale,
            offset=self.offset,
        )

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        raw = self.sensor.read_raw()
        yield self.sample(time_ns, weight=raw * self.scale + self.offset)


class Hx711Config(DriverConfig[Hx711], tag="hx711"):
    """One chip, two raw GPIO lines: `{ link, clock_line, data_line, scale, offset }`."""

    link: GpioLinkConfig | str  # type: ignore[valid-type]
    clock_line: int = Field(ge=0, description="PD_SCK: the host drives this.")
    data_line: int = Field(ge=0, description="DOUT: the chip drives this.")
    gain: Gain = "a128"
    scale: float = Field(default=1.0, description="Weight units per raw ADC count.")
    offset: float = 0.0

    def build(self, name: str, label: str | None = None) -> Hx711:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved to a chip before building")
        return Hx711(
            name,
            resolve(self.link),
            self.clock_line,
            self.data_line,
            self.gain,
            self.scale,
            self.offset,
            label=label,
        )


Hx711.config_type = Hx711Config  # the config is declared after the device it builds


__all__ = [
    "GAIN_PULSES",
    "WEIGHT",
    "Gain",
    "Hx711",
    "Hx711Config",
    "Hx711Sensor",
    "decode",
]
