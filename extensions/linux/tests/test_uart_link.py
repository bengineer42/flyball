"""FakeUart behaves like the serial port it stands in for."""

import pytest

from flyball_linux.links.uart import FakeUart


def test_fake_uart_write_read_round_trip():
    uart = FakeUart([b"1.23\r", b"OK\r"])
    uart.write(b"R\r")
    assert uart.read(5) == b"1.23\r"
    assert uart.read(3) == b"OK\r"
    assert uart.written == [b"R\r"]


def test_fake_uart_read_until_stops_at_terminator():
    uart = FakeUart([b"1.23\r", b"?FP,1\rextra"])
    assert uart.read_until(b"\r") == b"1.23\r"
    assert uart.read_until(b"\r") == b"?FP,1\r"


def test_fake_uart_single_reply_repeats():
    uart = FakeUart([b"OK\r"])
    assert uart.read_until() == b"OK\r"
    assert uart.read_until() == b"OK\r"


def test_fake_uart_unscripted_read_raises():
    uart = FakeUart()
    with pytest.raises(OSError):
        uart.read(1)
    with pytest.raises(OSError):
        uart.read_until()
