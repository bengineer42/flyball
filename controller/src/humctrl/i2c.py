from typing import Protocol, runtime_checkable


@runtime_checkable
class I2CBus(Protocol):
    """The subset of ``busio.I2C`` this package uses.

    Blinka ships no ``py.typed`` and no annotations, so importing ``busio.I2C``
    as a type gives a name and no checking. This is narrow on purpose: it keeps
    test fakes substitutable.
    """

    def try_lock(self) -> bool: ...
    def unlock(self) -> None: ...
    def writeto(self, address: int, buffer: bytes) -> None: ...
    def readfrom_into(self, address: int, buffer: bytearray) -> None: ...
