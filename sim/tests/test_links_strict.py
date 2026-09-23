"""The scripted buses answer a read exactly as long as asked for, or fail like a bus does.

A real `/dev/i2c-N` or spidev read returns the length asked for or raises; a fake that
quietly returns fewer bytes hides a driver that asks for the wrong length.
"""

from __future__ import annotations

import pytest

from flyball_sim.links import FakeI2c, FakeI2cConfig, FakeSpi


class TestFakeI2cLengths:
    def test_a_register_read_longer_than_the_script_is_an_error(self):
        bus = FakeI2c(registers={0x76: {0x88: [1, 2]}})
        with pytest.raises(OSError, match="3 bytes"):
            bus.read_register(0x76, 0x88, 3)

    def test_a_raw_read_longer_than_the_reply_is_an_error(self):
        bus = FakeI2c(replies={0x77: [[1, 2]]})
        with pytest.raises(OSError, match="3 bytes"):
            bus.read(0x77, 3)

    def test_a_longer_script_is_cut_to_the_length_asked_for(self):
        bus = FakeI2c(registers={0x76: {0x88: [1, 2, 3]}}, replies={0x77: [[4, 5, 6]]})
        assert bus.read_register(0x76, 0x88, 2) == b"\x01\x02"
        assert bus.read(0x77, 1) == b"\x04"

    def test_short_reads_opts_back_in_to_short_replies(self):
        bus = FakeI2c(registers={0x76: {0x88: [1, 2]}}, replies={0x77: [[1]]}, short_reads=True)
        assert bus.read_register(0x76, 0x88, 3) == b"\x01\x02"
        assert bus.read(0x77, 3) == b"\x01"

    def test_a_rig_file_bus_is_strict(self):
        bus = FakeI2cConfig(replies={0x77: [[1]]}).build()
        with pytest.raises(OSError, match="2 bytes"):
            bus.read(0x77, 2)


class TestFakeI2cRegisterMap:
    def test_a_burst_runs_on_into_the_next_register(self):
        bus = FakeI2c(registers={0x76: {0x88: [1, 2], 0x8A: [3, 4]}})
        assert bus.read_register(0x76, 0x88, 4) == b"\x01\x02\x03\x04"

    def test_a_read_can_start_inside_a_block(self):
        bus = FakeI2c(registers={0x76: {0x88: [1, 2, 3], 0x8B: [4]}})
        assert bus.read_register(0x76, 0x89, 3) == b"\x02\x03\x04"

    def test_a_register_of_its_own_wins_over_a_block_that_covers_it(self):
        bus = FakeI2c(registers={0x76: {0x88: [1, 2, 3], 0x89: [9]}})
        assert bus.read_register(0x76, 0x89, 1) == b"\x09"

    def test_a_gap_ends_the_burst(self):
        bus = FakeI2c(registers={0x76: {0x88: [1, 2], 0x8B: [3]}})
        with pytest.raises(OSError, match="3 bytes"):
            bus.read_register(0x76, 0x88, 3)

    def test_an_unscripted_register_is_still_no_device(self):
        bus = FakeI2c(registers={0x76: {0x88: [1]}})
        with pytest.raises(OSError, match="no device"):
            bus.read_register(0x76, 0x90, 1)


class TestFakeSpiLengths:
    def test_a_scripted_reply_shorter_than_the_transfer_is_an_error(self):
        spi = FakeSpi([[0, 1]])
        with pytest.raises(OSError, match="3 bytes"):
            spi.transfer([1, 2, 3])

    def test_a_function_reply_shorter_than_the_transfer_is_an_error(self):
        spi = FakeSpi(lambda sent: [0])
        with pytest.raises(OSError, match="2 bytes"):
            spi.transfer([1, 2])

    def test_an_unscripted_bus_clocks_in_zeros(self):
        assert FakeSpi().transfer([9, 9]) == b"\x00\x00"

    def test_short_reads_pads_with_zeros_as_before(self):
        assert FakeSpi([[7]], short_reads=True).transfer([1, 2]) == b"\x07\x00"
