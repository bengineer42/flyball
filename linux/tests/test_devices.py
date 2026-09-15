"""Each device against its fake bus, to the byte."""

import pytest

from flyball_linux.devices.gpio import GpioActuator, GpioReader
from flyball_linux.devices.i2c_table import I2cActuator, I2cReader, Register
from flyball_linux.devices.onewire import Ds18b20Reader, parse_w1_slave
from flyball_linux.devices.pwm import PwmActuator
from flyball_linux.links.gpio import FakeGpio
from flyball_linux.links.i2c import FakeI2c
from flyball_linux.links.onewire import FakeOneWire
from flyball_linux.links.pwm import FakePwm


class TestRegister:
    def test_decodes_signed_big_endian_with_scale(self):
        r = Register(address=0, length=2, signed=True, scale=0.0078125, unit="°C")
        assert r.decode(b"\x0c\x80") == pytest.approx(25.0)  # TMP117: 0x0C80 = 3200 LSB
        assert r.decode(b"\xff\x80") == pytest.approx(-1.0)

    def test_shift_and_little_endian(self):
        r = Register(address=0, length=2, byteorder="little", shift=4, scale=0.0625)
        assert r.decode(b"\x90\x01") == pytest.approx(1.5625)  # 0x0190 >> 4 = 25 LSB

    def test_encode_round_trips_and_quantises(self):
        r = Register(address=1, length=2, scale=0.5, offset=-10.0)
        assert r.encode(25.0) == b"\x00\x46"  # (25 + 10) / 0.5 = 70
        assert r.decode(r.encode(25.3)) == pytest.approx(25.5)

    def test_wrong_length_is_an_error(self):
        with pytest.raises(ValueError, match="wants 2 bytes"):
            Register(address=0).decode(b"\x00")


def test_i2c_reader_reads_every_register_into_one_sample(fresh):
    bus = FakeI2c(registers={0x48: {0x00: [0x0C, 0x80], 0x02: [0x00, 0x2A]}})
    reader = I2cReader(
        fresh("tmp"),
        bus,
        0x48,
        {
            "temperature": Register(address=0, signed=True, scale=0.0078125, unit="°C"),
            "status": Register(address=2, length=2),
        },
    )
    (sample,) = reader.read(7)
    values = {m.name: v for m, v in sample.values.items()}
    assert values["temperature"] == pytest.approx(25.0) and values["status"] == 42
    assert sample.time_ns == 7 and reader.state.values["status"] == 42


def test_i2c_actuator_writes_the_demand_and_reports_what_the_chip_holds(fresh):
    bus = FakeI2c()
    dac = I2cActuator(
        fresh("dac"), bus, 0x60, Register(address=0x40, length=2, scale=0.001, unit="V")
    )
    assert dac.set_demand(1.2345) == pytest.approx(1.234)  # 1234.5 LSB rounds to even
    assert bus.written == [(0x60, 0x40, [0x04, 0xD2])]
    assert dac.state.demand == 1.2345 and dac.demand_unit.symbol == "V"


def test_gpio_reader_and_actuator(fresh):
    chip = FakeGpio(levels={17: True})
    reader = GpioReader(
        fresh("door"), chip, 17, pull_up=True, invert=True, measurand=fresh("level")
    )
    (sample,) = reader.read(1)
    assert next(iter(sample.values.values())) == 0.0 and reader.state.level is False
    relay = GpioActuator(fresh("relay"), chip, 18, threshold=0.5, invert=True)
    assert chip.levels[18] is True  # off, active-low
    relay.set_demand(0.7)
    assert chip.levels[18] is False and relay.state.on is True
    relay.off()
    assert relay.state.on is False and chip.sets[-1] == (18, True)


def test_gpio_actuator_can_switch_on_a_demand_in_the_loop_s_unit(fresh):
    chip = FakeGpio()
    relay = GpioActuator(fresh("heater"), chip, 4, threshold=21.0, unit="°C")
    assert relay.demand_unit.symbol == "°C"
    relay.set_demand(20.5)
    assert relay.state.on is False
    relay.set_demand(21.0)
    assert relay.state.on is True


def test_pwm_actuator_maps_demand_to_duty_within_limits(fresh):
    pwm = FakePwm()
    heater = PwmActuator(
        fresh("heater"), pwm, 0, frequency_hz=1000, limits=(0.0, 0.8), unit="°C", span=(10, 40)
    )
    assert pwm.enabled[0] is True and pwm.channels[0] == (1_000_000, 0)
    heater.set_demand(25.0)
    assert heater.state.duty == pytest.approx(0.5) and pwm.channels[0] == (1_000_000, 500_000)
    heater.set_demand(60.0)
    assert heater.state.duty == pytest.approx(0.8), "clamped to the limit"
    heater.set_frequency(2000)
    assert pwm.channels[0] == (500_000, 400_000)
    heater.off()
    assert pwm.enabled[0] is False and heater.state.duty == 0.0


def test_pwm_invert_and_bare_duty(fresh):
    pwm = FakePwm()
    led = PwmActuator(fresh("led"), pwm, 1, frequency_hz=100, invert=True)
    led.set_demand(0.25)
    assert pwm.channels[1] == (10_000_000, 7_500_000)
    with pytest.raises(ValueError):
        led.set_limits(0.5, 0.2)


class TestDs18b20:
    GOOD = "5e 01 4b 46 7f ff 0c 10 4e : crc=4e YES\n5e 01 4b 46 7f ff 0c 10 4e t=21875"

    def test_parses_the_kernel_text(self):
        assert parse_w1_slave(self.GOOD) == pytest.approx(21.875)
        assert parse_w1_slave(self.GOOD.replace("21875", "-1250")) == pytest.approx(-1.25)

    def test_a_failed_crc_is_a_hardware_error(self):
        from flyball.core.errors import HardwareError

        with pytest.raises(HardwareError, match="CRC"):
            parse_w1_slave(self.GOOD.replace("YES", "NO"))

    def test_reader(self, fresh):
        bus = FakeOneWire({"28-1": self.GOOD})
        probe = Ds18b20Reader(fresh("soil"), bus, "28-1")
        (sample,) = probe.read(3)
        assert next(iter(sample.values.values())) == pytest.approx(21.875)
        assert probe.state.temperature == pytest.approx(21.875)
