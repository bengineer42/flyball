"""B24: a fake link's `blocking` is a config option now, not decided by `isinstance`.

A `fake_text` link with `blocking: true` must make its `scpi` device
`blocking = True` too, so the Writer thread and the `write_failed` path
actually run against it -- previously `isinstance(link, FakeTextLink)`
made every fake non-blocking regardless of configuration, so a sim or a
test could never exercise that path.
"""

from __future__ import annotations

import time

from flyball.runtime.config import RigConfig

from flyball_visa import FakeTextLink, Scpi, ScpiSignal


def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)


class TestFakeBlockingOption:
    def test_default_fake_is_not_blocking(self):
        link = FakeTextLink({"R?": "0"})
        dev = Scpi("d", link, {"x": ScpiSignal(query="R?", unit="V")})
        assert dev.blocking is False

    def test_fake_configured_blocking_makes_the_device_blocking(self):
        link = FakeTextLink({"R?": "0"}, blocking=True)
        dev = Scpi("d", link, {"x": ScpiSignal(query="R?", unit="V")})
        assert dev.blocking is True


class TestFakeBlockingGoesThroughTheWriter:
    def _rig_document(self) -> dict:
        return {
            "links": {"psu": {"tag": "fake_text", "blocking": True}},
            "devices": {
                "psu": {
                    "driver": "scpi",
                    "config": {
                        "link": "psu",
                        "channels": {
                            "set_voltage": {"write": "SOUR:VOLT {value:.3f}", "unit": "V"}
                        },
                    },
                }
            },
        }

    def test_a_blocking_fake_commits_on_the_writer_thread_not_the_delivery(self):
        rig = RigConfig.model_validate(self._rig_document()).build(start=False)
        psu = rig.devices["psu"]
        assert psu.blocking is True

        assert rig.demand(psu.root, {"set_voltage": 12.0}) == {}, "queued, not done inline"
        assert rig._writers, "a writer thread was started for the blocking fake"

        _wait_until(lambda: psu.link.written != [])
        assert psu.link.written == ["SOUR:VOLT 12.000"]
        rig.close()
