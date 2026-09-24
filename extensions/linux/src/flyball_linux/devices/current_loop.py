"""4-20mA current-loop instruments: a scaling layer on top of an existing ADC channel.

**Never run against real hardware.** This module is written from the standard
4-20mA current-loop convention used by process instruments (DO/O2 analyzers,
pressure and level transmitters) that offer no digital bus -- it has not been
verified against any physical loop, transmitter or sense resistor.

A 4-20mA loop is not a bus like I2C or SPI: it is a physical convention.
The instrument sinks a current proportional to its reading; that current is
turned into a voltage by dropping it across a precision sense resistor
(commonly 250 ohm, giving 1-5 V over the 4-20 mA span), and an ordinary ADC
reads that voltage. So this driver does not talk to hardware itself -- it
wraps an [Ads1115][flyball_chips.ads1115.Ads1115] or
[Mcp3008][flyball_chips.mcp3008.Mcp3008] device, configuring
its channel(s) to report milliamps (`volts / resistor_ohms * 1000`), then
maps milliamps onto the instrument's engineering-unit span and checks the
loop is alive.

Fault detection follows the NAMUR NE43 convention: a healthy loop stays
within roughly 3.8-20.5 mA, and a reading at or below ~3.6 mA or at or
above ~21 mA conventionally means a broken wire, a disconnected sensor or
a transmitter fault, not a real process value. [Unverified: NE43's exact
thresholds are widely cited in industrial-instrumentation practice but the
primary NAMUR document itself was not consulted here; 3.6/21.0 mA is the
commonly quoted approximation and is what this driver uses.]

A fault current is a good read of a value that is not one: the channel's
reading is a no-value, [invalid][flyball.foundation.device.novalue.invalid]
`("ne43_low", side="low")` or `("ne43_high", side="high")`, never a clamped,
wrong engineering value, and never a raise -- the ADC was read, so the read
does not count toward the device's failure budget, and one broken loop does
not take the other channels on the ADC offline. A controller regulating on
it freezes; an `alarm` band on it raises `band_unknown`.

Between the fault band and the measuring range (3.6-3.8 mA, 20.5-21 mA,
NE43's saturation region) the transmitter is pinned at an end of its range:
the value is reported, [railed][flyball.foundation.device.novalue.railed] at
that end (the caveat `at_limit`), since the true process value may lie past
it. [Unverified: 3.8/20.5 mA, as for the fault thresholds.]

The ADC itself failing (a bus error) still raises: that is the transport.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from flyball.foundation.device import (
    Access,
    DriverConfig,
    Node,
    Readable,
    Sample,
    Signal,
    SignalSpec,
    Value,
    invalid,
    railed,
)
from flyball.foundation.quantities import Quantity
from flyball.hardware.scan import Scan
from flyball_chips import ads1115, mcp3008
from pydantic import BaseModel, ConfigDict, Field

LOW_FAULT_MA = 3.6
"""At or below this, NE43-style, the loop reads as broken (open circuit, dead sensor)."""
HIGH_FAULT_MA = 21.0
"""At or above this, NE43-style, the loop reads as broken (short, transmitter fault)."""
SATURATED_LOW_MA = 3.8
"""At or below this (above the fault band), NE43-style, the transmitter is pinned low."""
SATURATED_HIGH_MA = 20.5
"""At or above this (below the fault band), NE43-style, the transmitter is pinned high."""


class CurrentLoopChannel(BaseModel):
    """One instrument on one ADC channel: the sense resistor, and the 4-20mA span.

    `value = mA * scale + offset`, the same shape as
    [Register][flyball_linux.devices.i2c_table.Register]'s `scale`/`offset`,
    with milliamps standing in for Register's raw counts. For "4 mA = 0 ppm
    O2, 20 mA = 25% O2": `scale = 25.0 / 16.0`, `offset = -4 * scale`, i.e.
    `scale=1.5625, offset=-6.25`.
    """

    model_config = ConfigDict(extra="forbid")

    channel: int = Field(ge=0, description="The channel on the wrapped ADC.")
    quantity: str | None = Field(
        default=None, description="The quantity's own name, if it differs from the signal's."
    )
    unit: str = "1"
    resistor_ohms: float = Field(default=250.0, gt=0, description="The loop's sense resistor.")
    scale: float = Field(default=1.0, description="Signal units per milliamp.")
    offset: float = 0.0
    low_ma: float = Field(
        default=LOW_FAULT_MA, description="At or below this, the loop is faulted."
    )
    high_ma: float = Field(
        default=HIGH_FAULT_MA, description="At or above this, the loop is faulted."
    )


class CurrentLoop(Readable):
    """Each of `channels` becomes one `[RP]` signal, read via the wrapped ADC and fault-checked."""

    def __init__(
        self,
        name: str,
        adc: ads1115.Ads1115 | mcp3008.Mcp3008,
        channels: Mapping[str, CurrentLoopChannel],
        label: str | None = None,
    ) -> None:
        super().__init__(name, label)
        if not channels:
            raise ValueError(f"{name}: a current_loop reads at least one channel")
        self.adc = adc
        self.channels = dict(channels)
        self._scan = Scan()
        self.bind([
            SignalSpec(name=key, quantity=Quantity(c.quantity or key, c.unit), access=Access.RP)
            for key, c in self.channels.items()
        ])
        self._signals = [self.signals[key] for key in self.channels]

    @property
    def config(self) -> CurrentLoopConfig:
        return CurrentLoopConfig(adc=self.adc.config, channels=self.channels)

    def _engineering(self, signal: Signal, milliamps: dict[str, float]) -> Value:
        """The channel's value: engineering units, railed at a saturated end, or invalid."""
        channel = self.channels[signal.name]
        ma = milliamps[signal.name]
        if ma <= channel.low_ma:
            return invalid("ne43_low", side="low")
        if ma >= channel.high_ma:
            return invalid("ne43_high", side="high")
        value = ma * channel.scale + channel.offset
        if ma <= SATURATED_LOW_MA:
            return railed(value, "low")
        if ma >= SATURATED_HIGH_MA:
            return railed(value, "high")
        return value

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """One ADC read per due signal, mapped to milliamps then to engineering units.

        A loop current in the fault band (at or below ~3.6 mA, at or above ~21
        mA by default) is `invalid` with its side, not a raise: the ADC was read.
        One in the saturation region is its value, `railed` at that end.

        Raises:
            HardwareError: The wrapped ADC's read failed.
        """
        due = self._scan.due(self._signals, time_ns, whole=node is not None)
        if not due:
            return
        (adc_sample,) = self.adc.read(time_ns, self.adc.root)
        milliamps = adc_sample.by_name()
        yield Sample(
            self.root, time_ns, {signal: self._engineering(signal, milliamps) for signal in due}
        )


def _adc_channels(
    adc: ads1115.Ads1115Config | mcp3008.Mcp3008Config, channels: Mapping[str, CurrentLoopChannel]
) -> dict[str, ads1115.Channel] | dict[str, mcp3008.Channel]:
    """The wrapped ADC's own `channels:` tree: each reports milliamps, not volts."""
    if isinstance(adc, ads1115.Ads1115Config):
        return {
            key: ads1115.Channel(
                channel=c.channel, unit="mA", scale=1000.0 / c.resistor_ohms, offset=0.0
            )
            for key, c in channels.items()
        }
    return {
        key: mcp3008.Channel(
            channel=c.channel, unit="mA", scale=1000.0 / c.resistor_ohms, offset=0.0
        )
        for key, c in channels.items()
    }


class CurrentLoopConfig(DriverConfig[CurrentLoop], type="current_loop"):
    """`driver: current_loop`. Wraps an `ads1115`/`mcp3008` channel; `channels` is its own tree.

    ```yaml
    oxygen:
      driver: current_loop
      adc:
        driver: ads1115
        link: i2c1
        address: 0x48
      channels:
        o2:
          channel: 0
          unit: "%"
          resistor_ohms: 250.0
          scale: 1.5625   # 25% over the 16 mA span
          offset: -6.25   # so 4 mA -> 0 %, 20 mA -> 25 %
    ```
    """

    adc: ads1115.Ads1115Config | mcp3008.Mcp3008Config = Field(
        description="The ADC the loop's sense-resistor voltage is read on."
    )
    channels: dict[str, CurrentLoopChannel]

    def build(self, name: str, label: str | None = None) -> CurrentLoop:
        generated = _adc_channels(self.adc, self.channels)
        adc_config = self.adc.model_copy(update={"channels": generated})
        if isinstance(adc_config.link, str):
            raise TypeError(f"link {adc_config.link!r} must be resolved to a bus before building")
        adc = adc_config.build(f"{name}.adc")
        return CurrentLoop(name, adc, self.channels, label=label)


CurrentLoop.config_type = CurrentLoopConfig  # the config is declared after the device it builds


__all__ = [
    "HIGH_FAULT_MA",
    "LOW_FAULT_MA",
    "SATURATED_HIGH_MA",
    "SATURATED_LOW_MA",
    "CurrentLoop",
    "CurrentLoopChannel",
    "CurrentLoopConfig",
]
