"""VISA and serial text-instrument links and the generic `scpi` device.

Neither `VisaLink`/`SerialLink` nor their configs import pyvisa/pyserial at
module load: only building a real one does, so the `visa`/`serial` extras
are needed only where a real link is actually built. `FakeTextLink` needs
neither.
"""

from ._links import (
    TEXT_LINKS,
    FakeTextLink,
    FakeTextLinkConfig,
    SerialLink,
    SerialLinkConfig,
    TextLinkConfig,
    VisaLink,
    VisaLinkConfig,
)
from ._scpi import Parser, Scpi, ScpiConfig, ScpiSignal, parse_float

__all__ = [
    "TEXT_LINKS",
    "FakeTextLink",
    "FakeTextLinkConfig",
    "Parser",
    "Scpi",
    "ScpiConfig",
    "ScpiSignal",
    "SerialLink",
    "SerialLinkConfig",
    "TextLinkConfig",
    "VisaLink",
    "VisaLinkConfig",
    "parse_float",
]
