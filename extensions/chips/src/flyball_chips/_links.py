"""Per-protocol `link` config unions this package's chips validate against.

`extensions/chips` depends on `flyball` and `flyball-sim` (for its fakes),
never on `extensions/linux` (the real buses). A chip's `link` field is
almost always a string naming an entry in the rig's own top-level `links:`
section (built dynamically from every installed package's registered tags,
real or fake, regardless of which packages a chip driver itself imports);
these unions exist only to type the rarer inline-config case, so they only
need to know about this package's own dependency, `fake_*`, not a real bus.
"""

from __future__ import annotations

from flyball.foundation.config import Config
from flyball_sim.links import FakeGpioConfig, FakeI2cConfig, FakeSpiConfig, FakeUartConfig

I2cLinkConfig = Config.union(FakeI2cConfig)
SpiLinkConfig = Config.union(FakeSpiConfig)
GpioLinkConfig = Config.union(FakeGpioConfig)
UartLinkConfig = Config.union(FakeUartConfig)

__all__ = ["GpioLinkConfig", "I2cLinkConfig", "SpiLinkConfig", "UartLinkConfig"]
