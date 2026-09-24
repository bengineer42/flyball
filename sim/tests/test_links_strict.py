"""The scripted buses behave like the buses they stand in for.

A real `/dev/i2c-N` or spidev read returns the length asked for or raises; a fake that
quietly returns fewer bytes hides a driver that asks for the wrong length. A serial port
is a byte stream, not a queue of messages.
"""

from __future__ import annotations

import pytest

from flyball_sim.links import FakeI2c, FakeI2cConfig, FakeSpi, FakeUart


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


class TestFakeUartStream:
    def test_a_reply_read_in_pieces_is_one_stream(self):
        uart = FakeUart([b"\xff\x86\x01\x90", b"\x00\x00"])
        assert uart.read(1) == b"\xff"
        assert uart.read(4) == b"\x86\x01\x90\x00"
        assert uart.read(1) == b"\x00"

    def test_bytes_after_a_terminator_wait_for_the_next_read(self):
        uart = FakeUart([b"7.00\r*OK\r"])
        assert uart.read_until(b"\r") == b"7.00\r"
        assert uart.read_until(b"\r") == b"*OK\r"

    def test_a_line_split_across_replies_is_joined(self):
        uart = FakeUart([b"7.", b"00\r", b"end\r"])
        assert uart.read_until(b"\r") == b"7.00\r"

    def test_the_last_reply_repeats_so_a_rig_file_probe_reads_forever(self):
        uart = FakeUart([b"6.200\r"])
        assert [uart.read_until(b"\r") for _ in range(3)] == [b"6.200\r"] * 3

    def test_a_terminator_that_never_comes_returns_what_arrived_like_a_timeout(self):
        uart = FakeUart([b"abc"])
        assert uart.read_until(b"\r") == b"abc"

    def test_an_unscripted_port_raises(self):
        with pytest.raises(OSError):
            FakeUart().read(1)
        with pytest.raises(OSError):
            FakeUart().read_until()
