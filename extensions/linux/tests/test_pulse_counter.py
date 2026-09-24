"""A pulse counter against the fake GPIO link, to pulses and litres/minute."""

import pytest
from flyball.model.catalog import get_catalog

from flyball_linux.devices.pulse_counter import PulseCounter
from flyball_linux.links.gpio import FakeGpio

NS = 1_000_000_000


def test_claims_the_line_for_edge_detection():
    chip = FakeGpio()
    PulseCounter("flow", chip, 21, pulses_per_litre=450)
    assert chip.claimed[21] == "edge"


def test_first_read_has_no_rate_yet_but_counts_pulses():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, pulses_per_litre=450)
    chip.pulse(21, 45)
    (sample,) = meter.read(0)
    assert sample.by_name() == {"rate": 0.0, "count": 45.0}


def test_rate_is_litres_per_minute_from_pulses_over_elapsed_time():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, pulses_per_litre=450)
    list(meter.read(0))
    chip.pulse(21, 450)  # 1 litre
    (sample,) = meter.read(30 * NS)  # in 30 s -> 2 L/min
    assert sample.by_name() == pytest.approx({"rate": 2.0, "count": 450.0})


def test_count_keeps_rising_across_reads():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, pulses_per_litre=450)
    list(meter.read(0))
    chip.pulse(21, 100)
    list(meter.read(1 * NS))
    chip.pulse(21, 50)
    (sample,) = meter.read(2 * NS)
    assert sample.by_name()["count"] == 150.0


def test_no_pulses_is_zero_rate():
    chip = FakeGpio()
    meter = PulseCounter("flow", chip, 21, pulses_per_litre=450)
    list(meter.read(0))
    (sample,) = meter.read(10 * NS)
    assert sample.by_name() == {"rate": 0.0, "count": 0.0}


def test_reading_an_unclaimed_line_is_an_error():
    chip = FakeGpio()
    with pytest.raises(OSError):
        chip.count_edges(5)


def test_type_builds():
    assert get_catalog().devices["pulse_counter"] is not None
