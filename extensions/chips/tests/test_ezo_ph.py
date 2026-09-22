"""EZO-pH over a scripted UART: the `*OK`-prefixed and bare-reading cases, and errors."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeUart

from flyball_chips import ezo_ph


class TestParsePh:
    def test_parses_a_realistic_ascii_reading(self):
        assert ezo_ph.parse_ph(b"7.002\r") == pytest.approx(7.002)

    def test_a_status_frame_is_not_a_reading(self):
        with pytest.raises(HardwareError, match="status frame"):
            ezo_ph.parse_ph(b"*ER\r")

    def test_a_malformed_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="not a decimal pH value"):
            ezo_ph.parse_ph(b"garbage\r")


class TestEzoPhProbe:
    def test_read_sends_r_and_decodes_the_reply(self):
        uart = FakeUart([b"4.003\r"])
        probe = ezo_ph.EzoPhProbe(uart, sleep=False)
        assert probe.read() == pytest.approx(4.003)
        assert uart.written == [b"R\r"]

    def test_read_skips_a_leading_ok_response_code(self):
        uart = FakeUart([b"*OK\r", b"9.180\r"])
        probe = ezo_ph.EzoPhProbe(uart, sleep=False)
        assert probe.read() == pytest.approx(9.180)

    def test_an_error_response_code_raises(self):
        uart = FakeUart([b"*ER\r"])
        probe = ezo_ph.EzoPhProbe(uart, sleep=False)
        with pytest.raises(HardwareError, match=r"\*ER"):
            probe.read()

    def test_an_unscripted_reply_is_an_os_error(self):
        probe = ezo_ph.EzoPhProbe(FakeUart(), sleep=False)
        with pytest.raises(OSError):
            probe.read()


class TestEzoPh:
    def test_declares_ph_on_the_root(self):
        probe = ezo_ph.EzoPh("water", FakeUart(), sleep=False)
        assert {p: str(s.access) for p, s in probe.signals.items()} == {
            "conditions": "rp",
            "ph": "rp",
        }
        assert probe.signals["ph"].unit.symbol == "pH"
        assert probe.nodes == {}

    def test_read_collects_one_sample(self):
        uart = FakeUart([b"6.850\r"])
        probe = ezo_ph.EzoPh("water", uart, sleep=False)
        (sample,) = probe.read(9)
        assert sample.node is probe.root and sample.time_ns == 9
        assert sample.by_name() == {"ph": pytest.approx(6.850)}
        assert uart.written == [b"R\r"]

    def test_config_reports_the_tag(self):
        probe = ezo_ph.EzoPh("water", FakeUart(), sleep=False)
        assert isinstance(probe.config, ezo_ph.EzoPhConfig)
        assert probe.config.link == ""
