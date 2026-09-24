"""`Rig.close()`: idempotent teardown that also closes links (ENG-15).

Also the scaled clock that must not restart behind its last session (N19).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from flyball.rig import Rig
from flyball.runner.starting import start_with_store
from flyball.runtime.config import load_rig_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


class FakeWriter:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


class FakeLink:
    def __init__(self, *, raises: bool = False) -> None:
        self.closes = 0
        self.raises = raises

    def close(self) -> None:
        self.closes += 1
        if self.raises:
            raise RuntimeError("bus wedged")


class NoCloseLink:
    """A link with no `close`, as most links have none: `close()` must not require it."""


class TestClose:
    def test_close_is_idempotent(self, rig: Rig, fresh) -> None:
        writer = FakeWriter()
        rig._writers[object()] = writer  # type: ignore[index]
        link = FakeLink()
        rig.links[fresh("bus")] = link

        rig.close()
        rig.close()

        assert writer.stops == 1, "a second close must not stop what the first already stopped"
        assert link.closes == 1, "a second close must not close a link twice"

    def test_close_closes_every_link(self, rig: Rig, fresh) -> None:
        a, b = FakeLink(), FakeLink()
        rig.links[fresh("bus")] = a
        rig.links[fresh("bus")] = b

        rig.close()

        assert a.closes == 1 and b.closes == 1

    def test_close_skips_a_link_with_no_close(self, rig: Rig, fresh) -> None:
        rig.links[fresh("bus")] = NoCloseLink()

        rig.close()  # must not raise

    def test_close_carries_on_past_a_raising_link_and_logs_it(
        self, rig: Rig, fresh, caplog
    ) -> None:
        bad = FakeLink(raises=True)
        good = FakeLink()
        rig.links[fresh("bad")] = bad
        rig.links[fresh("good")] = good

        with caplog.at_level(logging.ERROR, logger="flyball.rig"):
            rig.close()

        assert bad.closes == 1 and good.closes == 1, "the good link still closes"
        assert any("close failed" in record.message for record in caplog.records)

    def test_close_does_not_touch_devices(self, rig: Rig, fresh) -> None:
        from test_rig_devices import Furnace

        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)

        rig.close()

        assert furnace.name in rig.devices, "close tears down links, not devices"


class TestScaledClockSeed:
    """N19: a scaled clock must not restart behind the last session it recorded."""

    def test_the_second_build_starts_at_or_after_the_first_s_recorded_end(self, tmp_path) -> None:
        config = load_rig_config(EXAMPLES / "oven.yaml")
        path = tmp_path / "s.sqlite"

        rig1, store1 = start_with_store(config, store_path=path)
        try:
            # A session that ended far ahead of real time, as a fast scaled clock would
            # leave one behind it.
            far_ahead_ns = time.time_ns() + 3600 * 1_000_000_000
            writer = store1.open_session(rig1.clock.now_ns())
            store1.end_session(writer.session.id, far_ahead_ns)
        finally:
            rig1.close()
            store1.close()

        rig2, store2 = start_with_store(config, store_path=path)
        try:
            assert rig2.clock.now_ns() >= far_ahead_ns
        finally:
            rig2.close()
            store2.close()

    def test_a_stepped_clock_is_left_at_wall_time_not_reseeded(self, tmp_path) -> None:
        from flyball.runtime.config import ClockEntry

        config = load_rig_config(EXAMPLES / "oven.yaml").model_copy(
            update={"clock": ClockEntry(stepped=True)}
        )
        path = tmp_path / "s.sqlite"

        rig1, store1 = start_with_store(config, store_path=path)
        try:
            far_ahead_ns = time.time_ns() + 3600 * 1_000_000_000
            writer = store1.open_session(rig1.clock.now_ns())
            store1.end_session(writer.session.id, far_ahead_ns)
        finally:
            rig1.close()
            store1.close()

        rig2, store2 = start_with_store(config, store_path=path)
        try:
            assert rig2.clock.now_ns() < far_ahead_ns, "a stepped clock is not reseeded"
        finally:
            rig2.close()
            store2.close()
