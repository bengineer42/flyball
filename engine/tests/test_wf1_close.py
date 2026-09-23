"""`Rig.close()`: idempotent teardown (renamed from `stop`) that also closes links (ENG-15)."""

from __future__ import annotations

import logging

from flyball.rig import Rig


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
