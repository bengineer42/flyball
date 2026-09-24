"""B24: a fake link's `blocking` is a config option now, not decided by `isinstance`.

A `fake_i2c` link with `blocking: true` must make its `i2c_table` device
`blocking = True` too, so the Writer thread and the `write_failed` path
actually run against it -- previously `isinstance(link, FakeI2c)` made
every fake non-blocking regardless of configuration, so a sim or a test
could never exercise that path.
"""

from __future__ import annotations

import time

from flyball.runtime.config import RigConfig

from flyball_linux.devices.i2c_table import I2cTable, Register
from flyball_linux.links.i2c import FakeI2c


def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)


class TestFakeBlockingOption:
    def test_default_fake_is_not_blocking(self):
        bus = FakeI2c()
        chip = I2cTable("c", bus, 0x48, {"a": Register(address=0)})
        assert chip.blocking is False

    def test_fake_configured_blocking_makes_the_device_blocking(self):
        bus = FakeI2c(blocking=True)
        chip = I2cTable("c", bus, 0x48, {"a": Register(address=0)})
        assert chip.blocking is True


class TestFakeBlockingGoesThroughTheWriter:
    def _rig_document(self) -> dict:
        return {
            "links": {"i2c1": {"type": "fake_i2c", "blocking": True}},
            "devices": {
                "dac": {
                    "driver": "i2c_table",
                    "link": "i2c1",
                    "address": 0x60,
                    "registers": {"out": {"address": 0, "write": True}},
                }
            },
        }

    def test_a_blocking_fake_commits_on_the_writer_thread_not_the_delivery(self):
        rig = RigConfig.model_validate(self._rig_document()).build(start=False)
        dac = rig.devices["dac"]
        assert dac.blocking is True

        assert rig.write(dac.root, {"out": 5.0}) == {}, "queued, not done inline"
        assert rig._writers, "a writer thread was started for the blocking fake"

        _wait_until(lambda: dac.link.written != [])
        assert dac.link.written == [(0x60, 0, [0, 5])]
        rig.close()
