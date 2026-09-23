"""MH-Z19: fixed 9-byte binary frames over a scripted UART."""

import pytest
from flyball.foundation.errors import HardwareError
from flyball_sim.links import FakeUart

from flyball_chips import mhz19

NS = 1_000_000_000


class TestMhZ19:
    def test_request_appends_the_checksum_of_the_command_bytes(self):
        frame = mhz19.request()
        assert len(frame) == 9
        assert frame[:8] == mhz19.READ_CO2
        assert frame[8] == mhz19.checksum(frame)

    def test_encode_and_decode_round_trip(self):
        assert mhz19.decode(mhz19.encode(812)) == 812

    def test_a_bad_checksum_is_a_hardware_error(self):
        frame = bytearray(mhz19.encode(400))
        frame[8] ^= 0x01
        with pytest.raises(HardwareError, match="checksum"):
            mhz19.decode(bytes(frame))

    def test_a_short_frame_is_a_hardware_error(self):
        with pytest.raises(HardwareError, match="9"):
            mhz19.decode(b"\xff\x86\x00")

    def test_a_frame_not_starting_ff_86_is_a_hardware_error(self):
        bad = bytearray(mhz19.encode(400))
        bad[1] = 0x99
        bad[8] = mhz19.checksum(bytes(bad))
        with pytest.raises(HardwareError, match="FF 86"):
            mhz19.decode(bytes(bad))

    def test_declares_co2_on_the_root(self):
        sensor = mhz19.MhZ19("air", FakeUart())
        assert {p: str(s.access) for p, s in sensor.signals.items()} == {
            "conditions": "rp",
            "co2": "rp",
        }
        assert sensor.signals["co2"].unit.symbol == "ppm"

    def test_read_sends_the_request_and_decodes_the_reply(self):
        link = FakeUart(replies=[mhz19.encode(812)])
        sensor = mhz19.MhZ19("air", link)
        (sample,) = sensor.read(9)
        assert sample.node is sensor.root and sample.time_ns == 9
        assert sample.by_name() == {"co2": 812}
        assert link.written == [mhz19.request()]

    def test_a_frame_that_arrives_in_two_pieces_is_read_whole(self):
        frame = mhz19.encode(640)
        link = FakeUart(replies=[frame[:4], frame[4:], mhz19.encode(0)])
        (sample,) = mhz19.MhZ19("air", link).read(0)
        assert sample.by_name() == {"co2": 640}
