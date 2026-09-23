"""EZO-EC, EZO-ORP, EZO-DO over a scripted UART: `*OK`-prefixed and bare-reading

cases, and errors. One file for the three EZO-family chips added alongside
EZO-pH (`test_ezo_ph.py`), since they share `_ezo`'s wire protocol and only
differ in their reply's shape and decode.
"""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeUart

from flyball_chips import ezo_do, ezo_ec, ezo_orp


class TestParseEc:
    def test_parses_a_realistic_csv_reading(self):
        assert ezo_ec.parse_ec(b"1413.000,706.500,0.700,1.000\r") == pytest.approx((
            1413.000,
            706.500,
            0.700,
            1.000,
        ))

    def test_a_status_frame_is_not_a_reading(self):
        with pytest.raises(HardwareError, match="status frame"):
            ezo_ec.parse_ec(b"*ER\r")

    def test_fewer_than_four_fields_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="4-field"):
            ezo_ec.parse_ec(b"1413.000\r")

    def test_a_malformed_field_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="non-decimal field"):
            ezo_ec.parse_ec(b"1413.000,garbage,0.700,1.000\r")


class TestEzoEcProbe:
    def test_read_sends_r_and_decodes_the_reply(self):
        uart = FakeUart([b"1413.000,706.500,0.700,1.000\r"])
        probe = ezo_ec.EzoEcProbe(uart, sleep=False)
        assert probe.read() == pytest.approx((1413.000, 706.500, 0.700, 1.000))
        assert uart.written == [b"R\r"]

    def test_read_skips_a_leading_ok_response_code(self):
        uart = FakeUart([b"*OK\r1413.000,706.500,0.700,1.000\r"])  # one stream, as on the wire
        probe = ezo_ec.EzoEcProbe(uart, sleep=False)
        assert probe.read() == pytest.approx((1413.000, 706.500, 0.700, 1.000))

    def test_an_error_response_code_raises(self):
        uart = FakeUart([b"*ER\r"])
        probe = ezo_ec.EzoEcProbe(uart, sleep=False)
        with pytest.raises(HardwareError, match=r"\*ER"):
            probe.read()


class TestEzoEc:
    def test_declares_the_four_outputs_on_the_root(self):
        probe = ezo_ec.EzoEc("water", FakeUart(), sleep=False)
        assert {p: str(s.access) for p, s in probe.signals.items()} == {
            "conductivity": "rp",
            "total_dissolved_solids": "rp",
            "salinity": "rp",
            "specific_gravity": "rp",
        }
        assert probe.signals["conductivity"].unit.symbol == "µS/cm"
        assert probe.nodes == {}

    def test_read_collects_one_sample(self):
        uart = FakeUart([b"1413.000,706.500,0.700,1.000\r"])
        probe = ezo_ec.EzoEc("water", uart, sleep=False)
        (sample,) = probe.read(9)
        assert sample.node is probe.root and sample.time_ns == 9
        assert sample.by_name() == pytest.approx({
            "conductivity": 1413.000,
            "total_dissolved_solids": 706.500,
            "salinity": 0.700,
            "specific_gravity": 1.000,
        })
        assert uart.written == [b"R\r"]

    def test_config_reports_the_tag(self):
        probe = ezo_ec.EzoEc("water", FakeUart(), sleep=False)
        assert isinstance(probe.config, ezo_ec.EzoEcConfig)
        assert probe.config.link == ""


class TestParseOrp:
    def test_parses_a_realistic_ascii_reading(self):
        assert ezo_orp.parse_orp(b"209.6\r") == pytest.approx(209.6)

    def test_a_negative_reading_parses(self):
        assert ezo_orp.parse_orp(b"-45.2\r") == pytest.approx(-45.2)

    def test_a_status_frame_is_not_a_reading(self):
        with pytest.raises(HardwareError, match="status frame"):
            ezo_orp.parse_orp(b"*ER\r")

    def test_a_malformed_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="not a decimal mV value"):
            ezo_orp.parse_orp(b"garbage\r")


class TestEzoOrpProbe:
    def test_read_sends_r_and_decodes_the_reply(self):
        uart = FakeUart([b"209.6\r"])
        probe = ezo_orp.EzoOrpProbe(uart, sleep=False)
        assert probe.read() == pytest.approx(209.6)
        assert uart.written == [b"R\r"]

    def test_read_skips_a_leading_ok_response_code(self):
        uart = FakeUart([b"*OK\r-102.3\r"])  # one stream, as on the wire
        probe = ezo_orp.EzoOrpProbe(uart, sleep=False)
        assert probe.read() == pytest.approx(-102.3)

    def test_an_error_response_code_raises(self):
        uart = FakeUart([b"*ER\r"])
        probe = ezo_orp.EzoOrpProbe(uart, sleep=False)
        with pytest.raises(HardwareError, match=r"\*ER"):
            probe.read()


class TestEzoOrp:
    def test_declares_orp_on_the_root(self):
        probe = ezo_orp.EzoOrp("water", FakeUart(), sleep=False)
        assert {p: str(s.access) for p, s in probe.signals.items()} == {
            "orp": "rp",
        }
        assert probe.signals["orp"].unit.symbol == "mV"
        assert probe.nodes == {}

    def test_read_collects_one_sample(self):
        uart = FakeUart([b"209.6\r"])
        probe = ezo_orp.EzoOrp("water", uart, sleep=False)
        (sample,) = probe.read(9)
        assert sample.node is probe.root and sample.time_ns == 9
        assert sample.by_name() == {"orp": pytest.approx(209.6)}
        assert uart.written == [b"R\r"]

    def test_config_reports_the_tag(self):
        probe = ezo_orp.EzoOrp("water", FakeUart(), sleep=False)
        assert isinstance(probe.config, ezo_orp.EzoOrpConfig)
        assert probe.config.link == ""


class TestParseDo:
    def test_parses_a_realistic_ascii_reading(self):
        assert ezo_do.parse_do(b"7.82\r") == pytest.approx(7.82)

    def test_a_status_frame_is_not_a_reading(self):
        with pytest.raises(HardwareError, match="status frame"):
            ezo_do.parse_do(b"*ER\r")

    def test_a_csv_reading_is_rejected(self):
        with pytest.raises(HardwareError, match="multi-field CSV"):
            ezo_do.parse_do(b"7.82,98.3\r")

    def test_a_malformed_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="not a decimal mg/L value"):
            ezo_do.parse_do(b"garbage\r")


class TestEzoDoProbe:
    def test_read_sends_r_and_decodes_the_reply(self):
        uart = FakeUart([b"7.82\r"])
        probe = ezo_do.EzoDoProbe(uart, sleep=False)
        assert probe.read() == pytest.approx(7.82)
        assert uart.written == [b"R\r"]

    def test_read_skips_a_leading_ok_response_code(self):
        uart = FakeUart([b"*OK\r9.09\r"])  # one stream, as on the wire
        probe = ezo_do.EzoDoProbe(uart, sleep=False)
        assert probe.read() == pytest.approx(9.09)

    def test_an_error_response_code_raises(self):
        uart = FakeUart([b"*ER\r"])
        probe = ezo_do.EzoDoProbe(uart, sleep=False)
        with pytest.raises(HardwareError, match=r"\*ER"):
            probe.read()


class TestEzoDo:
    def test_declares_dissolved_oxygen_on_the_root(self):
        probe = ezo_do.EzoDo("water", FakeUart(), sleep=False)
        assert {p: str(s.access) for p, s in probe.signals.items()} == {
            "dissolved_oxygen": "rp",
        }
        assert probe.signals["dissolved_oxygen"].unit.symbol == "mg/L"
        assert probe.nodes == {}

    def test_read_collects_one_sample(self):
        uart = FakeUart([b"7.82\r"])
        probe = ezo_do.EzoDo("water", uart, sleep=False)
        (sample,) = probe.read(9)
        assert sample.node is probe.root and sample.time_ns == 9
        assert sample.by_name() == {"dissolved_oxygen": pytest.approx(7.82)}
        assert uart.written == [b"R\r"]

    def test_config_reports_the_tag(self):
        probe = ezo_do.EzoDo("water", FakeUart(), sleep=False)
        assert isinstance(probe.config, ezo_do.EzoDoConfig)
        assert probe.config.link == ""
