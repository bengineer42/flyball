"""Links to instruments: what a generic device talks over.

Each kind is a protocol only. The fake for tests, the real implementation
that imports its driver only when built, and the typed configs that build
them from a file live with the driver that uses the protocol -- `RegisterLink`
with `modbus` in `extensions/modbus`, `TextLink` with `scpi` in
`extensions/visa` -- registered through the `flyball.configs` entry point
like any other optional package. A device takes the protocol; which
implementation it gets is the rig's business.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextLink(Protocol):
    """A line-oriented instrument: write a command, or write one and read the reply."""

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...


@runtime_checkable
class RegisterLink(Protocol):
    """A register map: read and write 16-bit holding registers."""

    def read_registers(self, address: int, count: int = 1, unit: int = 1) -> list[int]: ...

    def write_registers(self, address: int, values: list[int], unit: int = 1) -> None: ...


__all__ = ["RegisterLink", "TextLink"]
