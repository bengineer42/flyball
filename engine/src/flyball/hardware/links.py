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

from typing import Literal, Protocol, runtime_checkable


@runtime_checkable
class TextLink(Protocol):
    """A line-oriented instrument: write a command, or write one and read the reply."""

    def write(self, command: str) -> None: ...

    def query(self, command: str) -> str: ...


RegisterKind = Literal["holding", "input", "coil", "discrete"]
"""Which register table: `input` and `discrete` are read-only; `coil` and `discrete` are 0/1."""


@runtime_checkable
class RegisterLink(Protocol):
    """A register map: read and write 16-bit registers, or coils and discrete inputs as 0/1.

    `kind` is the register table: `holding` (the default), `input` and `discrete` are
    read-only, `coil` is read and written. Modbus picks the function code by it.
    """

    def read_registers(
        self, address: int, count: int = 1, unit: int = 1, kind: RegisterKind = "holding"
    ) -> list[int]: ...

    def write_registers(
        self, address: int, values: list[int], unit: int = 1, kind: RegisterKind = "holding"
    ) -> None: ...


__all__ = ["RegisterKind", "RegisterLink", "TextLink"]
