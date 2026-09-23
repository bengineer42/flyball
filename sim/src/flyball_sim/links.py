"""Scripted buses: `fake_i2c`/`fake_spi`/`fake_gpio`/`fake_uart`, for a rig with no hardware.

One layer lower than `sim_plant`/`sim_daq`/`sim_drive`: those fake the plant, these fake
the bus underneath a real driver, so a chip driver (`extensions/chips`) runs unmodified
against a scripted reply instead of a real `/dev/i2c-N`-style device. `extensions/linux`
supplies the real counterpart of each (`SmbusI2c`, `SpidevSpi`, `GpiodChip`, `SerialUart`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from flyball.foundation.config import Config
from flyball.hardware.gpio import GpioLink
from flyball.hardware.i2c import I2cLink
from flyball.hardware.spi import SpiLink
from flyball.hardware.uart import UartLink
from pydantic import Field

# region I2C


class FakeI2c:
    """Registers per address, and scripted replies per address for raw reads.

    `registers = {0x48: {0x00: [0x12, 0x34]}}` answers `read_register(0x48, 0, 2)`.
    The registers are one contiguous map: a block's bytes sit at consecutive
    addresses, so a burst runs on into the next block and a read can start inside
    one; a register scripted on its own wins over a block that covers it.
    `replies = {0x44: [[...6 bytes...]]}` answers successive `read(0x44, 6)`.
    Every write is kept.

    A read longer than the script raises `OSError`, as a real bus does rather than
    return fewer bytes; a longer script is cut to the length asked for.
    `short_reads=True` returns the short data instead, for testing a driver's own
    length check.
    """

    def __init__(
        self,
        registers: dict[int, dict[int, list[int]]] | None = None,
        replies: dict[int, list[list[int]]] | None = None,
        short_reads: bool = False,
        blocking: bool = False,
    ) -> None:
        self.registers = {a: dict(r) for a, r in (registers or {}).items()}
        self.replies = {a: list(r) for a, r in (replies or {}).items()}
        self.short_reads = short_reads
        self.blocking = blocking
        """Whether a device built over this link should run its writes on the Writer thread."""
        self.written: list[tuple[int, int | None, list[int]]] = []
        """`(address, register or None, data)` per write, in order."""

    def _at(self, address: int, register: int) -> list[int] | None:
        """The bytes from `register` to the end of the block holding it, or `None`."""
        blocks = self.registers.get(address, {})
        if register in blocks:
            return blocks[register]
        covering = [r for r, d in blocks.items() if r < register < r + len(d)]
        if not covering:
            return None
        start = max(covering)
        return blocks[start][register - start :]

    def _exact(self, data: Sequence[int], length: int, what: str) -> bytes:
        if len(data) < length and not self.short_reads:
            raise OSError(f"{what}: {length} bytes asked for, {len(data)} scripted")
        return bytes(data[:length])

    def read_register(self, address: int, register: int, length: int) -> bytes:
        data = self._at(address, register)
        if data is None:
            raise OSError(f"no device at 0x{address:02x} register 0x{register:02x}")
        out = list(data)
        while len(out) < length and data:
            data = self._at(address, register + len(out))
            if data is None:
                break
            out += data
        return self._exact(out, length, f"0x{address:02x} register 0x{register:02x}")

    def write_register(self, address: int, register: int, data: Sequence[int]) -> None:
        self.registers.setdefault(address, {})[register] = list(data)
        self.written.append((address, register, list(data)))

    def write(self, address: int, data: Sequence[int]) -> None:
        self.written.append((address, None, list(data)))

    def read(self, address: int, length: int) -> bytes:
        queue = self.replies.get(address)
        if not queue:
            raise OSError(f"no reply scripted for 0x{address:02x}")
        reply = queue[0] if len(queue) == 1 else queue.pop(0)  # the last reply repeats
        return self._exact(reply, length, f"0x{address:02x} read")


class FakeI2cConfig(Config[I2cLink], type="fake_i2c"):
    """A scripted bus, for a rig file that runs without hardware."""

    registers: dict[int, dict[int, list[int]]] = Field(default_factory=dict)
    replies: dict[int, list[list[int]]] = Field(default_factory=dict)
    blocking: bool = Field(
        default=False,
        description="Run this fake's writes on the Writer thread, as a real bus would.",
    )

    def build(self) -> I2cLink:
        return FakeI2c(self.registers, self.replies, blocking=self.blocking)


# endregion
# region SPI


class FakeSpi:
    """Answers each transfer from a list, or a function of the bytes sent; keeps every transfer.

    A transfer clocks in as many bytes as it sends: a reply shorter than that raises
    `OSError` (`short_reads=True` pads it with zeros instead), a longer one is cut.
    With no replies scripted at all, every transfer reads zeros.
    """

    def __init__(
        self,
        replies: list[list[int]] | Callable[[list[int]], Sequence[int]] | None = None,
        short_reads: bool = False,
    ) -> None:
        self.replies = replies if callable(replies) else list(replies or [])
        self.short_reads = short_reads
        self.sent: list[list[int]] = []

    def transfer(self, data: Sequence[int]) -> bytes:
        sent = list(data)
        self.sent.append(sent)
        if callable(self.replies):
            reply = list(self.replies(sent))
        elif not self.replies:
            return bytes(len(sent))
        else:
            reply = self.replies[0] if len(self.replies) == 1 else self.replies.pop(0)
        if len(reply) < len(sent) and not self.short_reads:
            raise OSError(f"spi transfer: {len(sent)} bytes sent, {len(reply)} scripted")
        return bytes(reply[: len(sent)]).ljust(len(sent), b"\0")


class FakeSpiConfig(Config[SpiLink], type="fake_spi"):
    replies: list[list[int]] = Field(default_factory=list)

    def build(self) -> SpiLink:
        return FakeSpi(self.replies)


# endregion
# region GPIO


class FakeGpio:
    """Lines as a dict; sets are kept in order. Reading an unclaimed line is an error."""

    def __init__(self, levels: dict[int, bool] | None = None) -> None:
        self.levels = dict(levels or {})
        self.claimed: dict[int, str] = {}
        self.sets: list[tuple[int, bool]] = []
        self._pending_edges: dict[int, int] = {}

    def claim_output(self, line: int, initial: bool = False) -> None:
        self.claimed[line] = "output"
        self.levels[line] = initial

    def claim_input(self, line: int, pull_up: bool | None = None) -> None:
        self.claimed[line] = "input"
        self.levels.setdefault(line, bool(pull_up))

    def set(self, line: int, value: bool) -> None:
        if self.claimed.get(line) != "output":
            raise OSError(f"line {line} is not claimed as an output")
        self.levels[line] = value
        self.sets.append((line, value))

    def get(self, line: int) -> bool:
        if line not in self.claimed:
            raise OSError(f"line {line} is not claimed")
        return self.levels[line]

    def claim_edge(self, line: int, debounce_s: float = 0.0, pull_up: bool | None = None) -> None:
        self.claimed[line] = "edge"
        self._pending_edges[line] = 0

    def pulse(self, line: int, n: int = 1) -> None:
        """Test-only: simulate `n` edges arriving on `line`, for `count_edges` to drain."""
        if self.claimed.get(line) != "edge":
            raise OSError(f"line {line} is not claimed for edge detection")
        self._pending_edges[line] += n

    def count_edges(self, line: int) -> int:
        if self.claimed.get(line) != "edge":
            raise OSError(f"line {line} is not claimed for edge detection")
        count = self._pending_edges[line]
        self._pending_edges[line] = 0
        return count


class FakeGpioConfig(Config[GpioLink], type="fake_gpio"):
    levels: dict[int, bool] = Field(default_factory=dict, description="Input levels by line.")

    def build(self) -> GpioLink:
        return FakeGpio(self.levels)


# endregion
# region UART


class FakeUart:
    r"""One scripted byte stream, fed from `replies` in order; every write is kept.

    The replies are joined end to end, as bytes arriving on a wire: a read takes
    exactly the bytes it asks for and leaves the rest, `read_until` stops at the
    terminator and leaves what follows it, and one line may span two replies. The
    last reply repeats for ever, so `replies = [b"6.200\r"]` is a probe that always
    reads 6.200. A `read_until` whose terminator never arrives returns what did
    arrive, as a real port does on its timeout.
    """

    def __init__(self, replies: list[bytes] | None = None) -> None:
        self.replies = list(replies or [])
        self.written: list[bytes] = []
        self._buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))

    def _pull(self) -> None:
        if not self.replies:
            raise OSError("no reply scripted")
        self._buffer += self.replies[0] if len(self.replies) == 1 else self.replies.pop(0)

    def _take(self, length: int) -> bytes:
        out = bytes(self._buffer[:length])
        del self._buffer[:length]
        return out

    def read(self, length: int) -> bytes:
        while len(self._buffer) < length:
            self._pull()
        return self._take(length)

    def read_until(self, terminator: bytes = b"\r") -> bytes:
        while (index := self._buffer.find(terminator)) == -1:
            never = len(self.replies) == 1 and terminator not in self.replies[0] * 2
            self._pull()
            if never:
                return self._take(len(self._buffer))
        return self._take(index + len(terminator))


class FakeUartConfig(Config[UartLink], type="fake_uart"):
    """A scripted port, for a rig file that runs without hardware."""

    replies: list[bytes] = Field(default_factory=list)

    def build(self) -> UartLink:
        return FakeUart(self.replies)


# endregion

FAKE_I2C_LINKS = (FakeI2cConfig,)
FAKE_SPI_LINKS = (FakeSpiConfig,)
FAKE_GPIO_LINKS = (FakeGpioConfig,)
FAKE_UART_LINKS = (FakeUartConfig,)

__all__ = [
    "FAKE_GPIO_LINKS",
    "FAKE_I2C_LINKS",
    "FAKE_SPI_LINKS",
    "FAKE_UART_LINKS",
    "FakeGpio",
    "FakeGpioConfig",
    "FakeI2c",
    "FakeI2cConfig",
    "FakeSpi",
    "FakeSpiConfig",
    "FakeUart",
    "FakeUartConfig",
]
