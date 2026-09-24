"""Each device against its fake bus, to the byte."""

import pytest
from flyball.foundation.device import Access, Role
from flyball.foundation.errors import ConflictError, HardwareError

from flyball_linux.devices.gpio import GpioLine
from flyball_linux.devices.i2c_table import I2cTable, Register
from flyball_linux.devices.onewire import Ds18b20, parse_w1_slave
from flyball_linux.devices.pwm import PwmChannel
from flyball_linux.links.gpio import FakeGpio
from flyball_linux.links.i2c import FakeI2c
from flyball_linux.links.onewire import FakeOneWire
from flyball_linux.links.pwm import FakePwm

NS = 1_000_000_000


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

    def test_write_makes_it_writable(self):
        assert Register(address=0).access is Access.RP
        assert Register(address=0, write=True).access is Access.RPW

    def test_a_writable_register_is_a_demand_unless_declared_a_setting(self):
        """C13: `role: setting` keeps a configuration register out of a controller's reach."""
        assert Register(address=0, write=True).signal_role is Role.DEMAND
        assert Register(address=0, write=True, role="setting").signal_role is Role.SETTING
        assert Register(address=0).signal_role is Role.READOUT
        with pytest.raises(ValueError, match="only a writable register"):
            Register(address=0, role="setting")


class TestI2cTable:
    def test_reads_every_register_into_one_sample(self):
        bus = FakeI2c(registers={0x48: {0x00: [0x0C, 0x80], 0x02: [0x00, 0x2A]}})
        chip = I2cTable(
            "tmp",
            bus,
            0x48,
            {
                "temperature": Register(address=0, signed=True, scale=0.0078125, unit="°C"),
                "status": Register(address=2, length=2),
            },
        )
        assert chip.blocking is False, "a fake bus never blocks"
        assert chip.signals["temperature"].unit.symbol == "°C"
        (sample,) = chip.read(7)
        assert sample.by_name() == {"temperature": pytest.approx(25.0), "status": 42.0}
        assert sample.time_ns == 7

    def test_only_due_signals_are_read(self):
        bus = FakeI2c(registers={0x48: {0x00: [0, 1], 0x01: [0, 2]}})
        chip = I2cTable("c", bus, 0x48, {"a": Register(address=0), "b": Register(address=1)})
        chip.poll_s = 1.0
        chip.signals["b"].set_meta(poll_s=10.0)
        assert [s.by_name() for s in chip.read(0)] == [{"a": 1.0, "b": 2.0}]
        assert [s.by_name() for s in chip.read(1 * NS)] == [{"a": 1.0}]

    def test_commit_writes_the_register_and_pushes_what_the_chip_holds(self):
        bus = FakeI2c()
        dac = I2cTable(
            "dac",
            bus,
            0x60,
            {"out": Register(address=0x40, length=2, scale=0.001, unit="V", write=True)},
        )
        out = dac.signals["out"]
        assert out.access is Access.RPW
        dac.apply(out, 1, 1.2345)
        dac.commit(1)
        assert bus.written == [(0x60, 0x40, [0x04, 0xD2])]
        assert out.value == pytest.approx(1.234), "1234.5 LSB rounds to even; differs, so pushed"
        assert dac.staged == {out: 1.2345}, "commit does not clear staged -- the rig does"
        (sample,) = dac.read(2)
        assert sample.by_name() == {"out": pytest.approx(1.234)}, "read back from the register"

    def test_no_registers_is_refused(self):
        with pytest.raises(ValueError, match="at least one register"):
            I2cTable("c", FakeI2c(), 0x48, {})


class TestGpioLine:
    def test_an_output_is_one_rpw_signal_driven_on_commit(self):
        chip = FakeGpio()
        relay = GpioLine("relay", chip, 18, invert=True)
        assert {p: str(s.access) for p, s in relay.signals.items()} == {
            "on": "rpw",
            "last.on": "rp",
            "last.off": "rp",
        }
        assert relay.signals["on"].limits == (0.0, 1.0)
        assert chip.claimed == {18: "output"} and chip.levels[18] is True, "off, active-low"
        on = relay.signals["on"]
        assert on.value == 0.0, "the initial demand's readback"
        relay.apply(on, 1, 0.7)
        relay.commit(1)
        assert chip.levels[18] is False and on.value == 1.0, "0.7 quantises to on; pushed"
        assert on.at_limit is None, "a switch has no rail"
        relay.apply(on, 2, 0.0)
        relay.commit(2)
        assert chip.sets[-1] == (18, True)
        assert list(relay.read(0)) == [], "an output has nothing to read"

    def test_on_and_off_commands_drive_the_line_directly(self):
        chip = FakeGpio()
        fan = GpioLine("fan", chip, 4)
        assert set(type(fan).commands) == {"on", "off"}, "on/off; the demand's tree is computed"
        fan.on()
        assert fan.signals["on"].value == 1.0 and chip.levels[4] is True
        fan.off()
        assert fan.signals["on"].value == 0.0 and chip.sets == [(4, True), (4, False)]

    def test_an_input_is_one_rp_signal(self):
        chip = FakeGpio(levels={17: True})
        door = GpioLine("door", chip, 17, direction="input", pull_up=True, invert=True)
        assert {p: str(s.access) for p, s in door.signals.items()} == {
            "level": "rp",
            "last.on": "rp",
            "last.off": "rp",
        }
        assert chip.claimed == {17: "input"}
        (sample,) = door.read(1)
        assert sample.by_name() == {"level": 0.0}
        with pytest.raises(ConflictError, match="input line"):
            door.on()

    def test_config_round_trips(self):
        line = GpioLine("x", FakeGpio(), 5, direction="input", pull_up=False)
        assert line.config.model_dump() == {
            "link": "",
            "line": 5,
            "direction": "input",
            "invert": False,
            "initial": False,
            "pull_up": False,
        }


class TestPwmChannel:
    def test_a_bare_channel_is_the_duty_itself(self):
        pwm = FakePwm()
        led = PwmChannel("led", pwm, 1, frequency_hz=100)
        drive = led.signals["drive"]
        assert str(drive.access) == "rpw" and drive.unit.symbol == "of full"
        assert drive.limits == (0.0, 1.0)
        assert pwm.enabled[1] is True and pwm.channels[1] == (10_000_000, 0), "off from the start"
        assert drive.value == 0.0, "the initial demand's readback"
        led.apply(drive, 1, 0.25)
        led.commit(1)
        assert pwm.channels[1] == (10_000_000, 2_500_000)

    def test_unit_and_span_map_the_signal_linearly_onto_the_duty(self):
        pwm = FakePwm()
        heater = PwmChannel(
            "heater", pwm, 0, frequency_hz=1000, unit="°C", quantity="temperature", span=(10, 40)
        )
        drive = heater.signals["drive"]
        assert drive.unit.symbol == "°C" and drive.quantity.name == "temperature"
        assert drive.limits == (10.0, 40.0), "the span is what a demand is clamped to"
        assert drive.value == 10.0, "duty 0 is the bottom of the span"
        heater.apply(drive, 1, 25.0)
        heater.commit(1)
        assert pwm.channels[0] == (1_000_000, 500_000)
        assert heater.fraction(40.0) == pytest.approx(1.0)
        assert heater.config.span == (10.0, 40.0) and heater.config.unit == "°C"

    def test_unit_without_span_or_a_falling_span_is_refused(self):
        with pytest.raises(ValueError, match="go together"):
            PwmChannel("h", FakePwm(), 0, unit="°C")
        with pytest.raises(ValueError, match="rising"):
            PwmChannel("h", FakePwm(), 0, unit="°C", span=(40, 10))

    def test_invert_frequency_and_off(self):
        pwm = FakePwm()
        led = PwmChannel("led", pwm, 1, frequency_hz=100, invert=True)
        drive = led.signals["drive"]
        led.apply(drive, 1, 0.25)
        led.commit(1)
        assert pwm.channels[1] == (10_000_000, 7_500_000)
        led.set_frequency(200)
        assert led.frequency_hz.value == 200
        assert pwm.channels[1] == (5_000_000, 3_750_000), "the duty is re-applied at the new period"
        with pytest.raises(ValueError):
            led.set_frequency(0)
        led.off()
        assert pwm.enabled[1] is False
        assert drive.value == 0.0, "off pushes the demand's readback to the bottom of the span"


class TestDs18b20:
    GOOD = "5e 01 4b 46 7f ff 0c 10 4e : crc=4e YES\n5e 01 4b 46 7f ff 0c 10 4e t=21875"

    def test_parses_the_kernel_text(self):
        assert parse_w1_slave(self.GOOD) == pytest.approx(21.875)
        assert parse_w1_slave(self.GOOD.replace("21875", "-1250")) == pytest.approx(-1.25)

    def test_a_failed_crc_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="CRC"):
            parse_w1_slave(self.GOOD.replace("YES", "NO"))

    def test_read(self):
        bus = FakeOneWire({"28-1": self.GOOD})
        probe = Ds18b20("soil", bus, "28-1")
        assert {p: str(s.access) for p, s in probe.signals.items()} == {
            "temperature": "rp",
        }
        (sample,) = probe.read(3)
        assert sample.by_name() == {"temperature": pytest.approx(21.875)}
        assert probe.config.device == "28-1"


class TestDeclaredOff:
    """What a stop writes: `off` only where the driver cannot be wrong."""

    def test_a_pwm_channel_declares_zero_duty_unless_inverted_or_across_zero(self):
        assert PwmChannel("a", FakePwm(), 0).signals["drive"].spec.off == 0.0
        spanned = PwmChannel("b", FakePwm(), 1, unit="°C", quantity="temperature", span=(10, 40))
        assert spanned.signals["drive"].spec.off == 10.0, "span[0] is 0 % duty"
        assert PwmChannel("c", FakePwm(), 2, invert=True).signals["drive"].spec.off is None
        bridge = PwmChannel("d", FakePwm(), 3, unit="W", quantity="power", span=(-50, 50))
        assert bridge.signals["drive"].spec.off is None, "span[0] is full reverse"

    def test_a_gpio_output_declares_zero_unless_inverted(self):
        assert GpioLine("fan", FakeGpio(), 4).signals["on"].spec.off == 0.0
        assert GpioLine("relay", FakeGpio(), 18, invert=True).signals["on"].spec.off is None

    def test_a_stepper_and_a_dosing_pump_are_stopped_by_their_stop_command(self):
        from flyball_linux.devices.dosing_pump import DosingPump
        from flyball_linux.devices.stepper import Stepper

        assert Stepper.stop_command == "stop"
        assert DosingPump.stop_command == "stop"
        assert PwmChannel.stop_command is None, "off disables the channel: not the stop"
