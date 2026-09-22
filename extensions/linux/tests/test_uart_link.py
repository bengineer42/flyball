"""FakeUart behaves like the serial port it stands in for; SerialUart against a stubbed pyserial."""

import sys
import types

import pytest

from flyball_linux.links.uart import FakeUart, SerialConfig, SerialUart


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


class FakeSerial:
    """Stands in for `serial.Serial`: records what it was built and called with."""

    instances: list["FakeSerial"] = []

    def __init__(self, port=None, baudrate=None, timeout=None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.written: list[bytes] = []
        self.closed = False
        FakeSerial.instances.append(self)

    def write(self, data):
        self.written.append(data)

    def read(self, length):
        return b"x" * length

    def read_until(self, terminator):
        return b"reply" + terminator

    def close(self):
        self.closed = True


@pytest.fixture
def fake_serial_module(monkeypatch):
    FakeSerial.instances = []
    module = types.SimpleNamespace(Serial=FakeSerial)
    monkeypatch.setitem(sys.modules, "serial", module)
    return FakeSerial


class TestSerialUart:
    def test_opens_the_port_with_the_given_settings(self, fake_serial_module):
        uart = SerialUart("/dev/ttyUSB0", baudrate=115200, timeout=2.0)
        (opened,) = fake_serial_module.instances
        assert opened.port == "/dev/ttyUSB0"
        assert opened.baudrate == 115200
        assert opened.timeout == 2.0
        assert uart.port == "/dev/ttyUSB0"

    def test_write_read_and_close_delegate_to_the_serial_port(self, fake_serial_module):
        uart = SerialUart("/dev/ttyUSB0")
        uart.write(b"R\r")
        assert fake_serial_module.instances[0].written == [b"R\r"]
        assert uart.read(3) == b"xxx"
        assert uart.read_until(b"\r") == b"reply\r"
        uart.close()
        assert fake_serial_module.instances[0].closed

    @pytest.mark.parametrize("port", ["", "   ", "bad\x00port", "bad\nport"])
    def test_an_invalid_port_is_refused(self, fake_serial_module, port):
        with pytest.raises(ValueError):
            SerialUart(port)


class TestSerialConfig:
    @pytest.mark.parametrize("port", ["", "   ", "bad\x00port", "bad\nport"])
    def test_an_invalid_port_is_refused(self, port):
        with pytest.raises(ValueError):
            SerialConfig(port=port)

    def test_build_opens_the_configured_port(self, fake_serial_module):
        link = SerialConfig(port="/dev/ttyUSB0", baudrate=19200, timeout=0.5).build()
        (opened,) = fake_serial_module.instances
        assert opened.port == "/dev/ttyUSB0"
        assert opened.baudrate == 19200
        assert opened.timeout == 0.5
        assert isinstance(link, SerialUart)
