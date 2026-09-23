"""The fakes behave like the buses they stand in for, and the configs build them."""

import pytest
from flyball.model.catalog import get_catalog

from flyball_linux.links.gpio import FakeGpio
from flyball_linux.links.i2c import FakeI2c
from flyball_linux.links.onewire import FakeOneWire
from flyball_linux.links.pwm import FakePwm, SysfsPwm
from flyball_linux.links.spi import FakeSpi


def test_fake_i2c_registers_and_raw_replies():
    bus = FakeI2c(registers={0x48: {0x00: [0x12, 0x34]}}, replies={0x44: [[1, 2, 3], [4, 5, 6]]})
    assert bus.read_register(0x48, 0x00, 2) == b"\x12\x34"
    bus.write_register(0x48, 0x01, [0xAB, 0xCD])
    assert bus.read_register(0x48, 0x01, 2) == b"\xab\xcd"
    bus.write(0x44, [0xFD])
    assert bus.read(0x44, 3) == b"\x01\x02\x03"
    assert bus.read(0x44, 3) == b"\x04\x05\x06" and bus.read(0x44, 3) == b"\x04\x05\x06"
    assert bus.written == [(0x48, 0x01, [0xAB, 0xCD]), (0x44, None, [0xFD])]
    with pytest.raises(OSError):
        bus.read_register(0x50, 0, 1)


def test_fake_spi_pads_and_scripts():
    spi = FakeSpi([[0, 1, 2]])
    assert spi.transfer([1, 0x80, 0]) == b"\x00\x01\x02"
    echo = FakeSpi(lambda sent: [b ^ 0xFF for b in sent])
    assert echo.transfer([0x0F]) == b"\xf0"
    assert FakeSpi().transfer([9, 9]) == b"\x00\x00"


def test_fake_gpio_needs_a_claim():
    chip = FakeGpio(levels={5: True})
    with pytest.raises(OSError):
        chip.get(5)
    chip.claim_input(5)
    assert chip.get(5) is True
    chip.claim_output(6)
    chip.set(6, True)
    assert chip.get(6) is True and chip.sets == [(6, True)]
    with pytest.raises(OSError):
        chip.set(5, True)


def test_fake_pwm_refuses_a_duty_beyond_the_period():
    pwm = FakePwm()
    pwm.configure(0, 1000, 250)
    assert pwm.channels[0] == (1000, 250)
    with pytest.raises(ValueError):
        pwm.configure(0, 1000, 1001)


def test_sysfs_pwm_exports_and_orders_writes(tmp_path):
    chip = tmp_path / "pwmchip0"
    chip.mkdir()
    (chip / "export").write_text("")
    pwm = SysfsPwm(0, tmp_path)
    # the kernel creates the dir and its period/duty_cycle/enable files together on
    # export (drivers/pwm/core.c); here they exist already, `enable` writable at once
    (chip / "pwm1").mkdir()
    (chip / "pwm1" / "period").write_text("0")
    (chip / "pwm1" / "duty_cycle").write_text("0")
    (chip / "pwm1" / "enable").write_text("0")
    pwm.configure(1, 1_000_000, 500_000)
    assert (chip / "export").read_text() == ""
    assert (chip / "pwm1" / "period").read_text() == "1000000"
    assert (chip / "pwm1" / "duty_cycle").read_text() == "500000"
    pwm.configure(1, 100_000, 10_000)  # shrinking: duty first, or the kernel refuses
    assert (chip / "pwm1" / "period").read_text() == "100000"
    pwm.enable(1, True)
    assert (chip / "pwm1" / "enable").read_text() == "1"
    with pytest.raises(OSError, match="does not exist"):
        SysfsPwm(3, tmp_path)


def test_fake_onewire_serves_texts_in_turn():
    bus = FakeOneWire({"28-1": ["a", "b"], "28-2": "c"})
    assert bus.devices() == ["28-1", "28-2"]
    assert [bus.read("28-1") for _ in range(3)] == ["a", "b", "b"]
    assert bus.read("28-2") == "c"
    with pytest.raises(OSError):
        bus.read("28-9")


@pytest.mark.parametrize("tag", ["fake_i2c", "fake_spi", "fake_gpio", "fake_pwm", "fake_onewire"])
def test_every_fake_has_a_tag_that_builds(tag):
    assert get_catalog().links[tag]().build() is not None


@pytest.mark.parametrize("tag", ["i2c", "spi", "gpio", "pwm", "onewire"])
def test_every_real_link_has_a_tag(tag):
    assert get_catalog().links[tag].config_tag == tag
