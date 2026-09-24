"""A pulse counter against the fake GPIO link: litres/minute and rpm from the same driver."""

import pytest
from flyball.model.catalog import get_catalog

from flyball_linux.devices.pulse_counter import PulseCounter
from flyball_linux.links.gpio import FakeGpio

NS = 1_000_000_000


def test_claims_the_line_for_edge_detection():
    chip = FakeGpio()
    PulseCounter("flow", chip, 21, unit="L/min", per_pulse=1 / 450)
    assert chip.claimed[21] == "edge"


def test_first_read_has_no_rate_yet_but_counts_pulses():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, unit="L/min", per_pulse=1 / 450)
    chip.pulse(21, 45)
    (sample,) = meter.read(0)
    assert sample.by_name() == {"rate": 0.0, "count": 45.0}


def test_rate_is_litres_per_minute_from_pulses_over_elapsed_time():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, unit="L/min", per_pulse=1 / 450)
    list(meter.read(0))
    chip.pulse(21, 450)  # 1 litre
    (sample,) = meter.read(30 * NS)  # in 30 s -> 2 L/min
    assert sample.by_name() == pytest.approx({"rate": 2.0, "count": 450.0})


def test_rate_in_millilitres_per_second():
    chip = FakeGpio()
    meter = PulseCounter("drip", chip, 21, unit="mL/s", per_pulse=0.1)
    list(meter.read(0))
    chip.pulse(21, 20)  # 2 mL
    (sample,) = meter.read(2 * NS)  # in 2 s -> 1 mL/s
    assert sample.by_name() == pytest.approx({"rate": 1.0, "count": 20.0})


def test_rate_in_hertz_is_a_plain_pulse_frequency():
    chip = FakeGpio()
    meter = PulseCounter("tacho", chip, 21, unit="Hz", per_pulse=1.0)
    list(meter.read(0))
    chip.pulse(21, 10)
    (sample,) = meter.read(2 * NS)  # 10 pulses in 2 s -> 5 Hz
    assert sample.by_name() == pytest.approx({"rate": 5.0, "count": 10.0})


def test_rate_in_rpm_from_a_two_pulse_per_revolution_encoder():
    chip = FakeGpio()
    meter = PulseCounter("tacho", chip, 21, unit="rpm", per_pulse=0.5)
    list(meter.read(0))
    chip.pulse(21, 20)  # 10 revolutions
    (sample,) = meter.read(int(0.5 * NS))  # 10 rev in 0.5 s -> 1200 rpm
    assert sample.by_name() == pytest.approx({"rate": 1200.0, "count": 20.0})


@pytest.mark.parametrize("unit", ["L", "sccm"])
def test_a_unit_it_cannot_rate_is_refused_at_build(unit):
    # Refused when the device is built, not on every read: a read that raises
    # would count against the device's read-failure budget and take it offline.
    with pytest.raises(ValueError, match="not a plain frequency"):
        PulseCounter("bad", FakeGpio(), 21, unit=unit, per_pulse=1.0)


def test_count_keeps_rising_across_reads():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, unit="L/min", per_pulse=1 / 450)
    list(meter.read(0))
    chip.pulse(21, 100)
    list(meter.read(1 * NS))
    chip.pulse(21, 50)
    (sample,) = meter.read(2 * NS)
    assert sample.by_name()["count"] == 150.0


def test_no_pulses_is_zero_rate():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, unit="L/min", per_pulse=1 / 450)
    list(meter.read(0))
    (sample,) = meter.read(10 * NS)
    assert sample.by_name() == {"rate": 0.0, "count": 0.0}


def test_reading_an_unclaimed_line_is_an_error():
    chip = FakeGpio()
    with pytest.raises(OSError):
        chip.count_edges(5)


def test_type_builds():
    assert get_catalog().devices["pulse_counter"] is not None
