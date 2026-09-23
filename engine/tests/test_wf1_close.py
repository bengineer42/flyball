"""`Rig.close()`: idempotent teardown (renamed from `stop`)."""

from __future__ import annotations

from flyball.rig import Rig


class FakeWriter:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


class TestClose:
    def test_close_is_idempotent(self, rig: Rig, fresh) -> None:
        writer = FakeWriter()
        rig._writers[object()] = writer  # type: ignore[index]

        rig.close()
        rig.close()

        assert writer.stops == 1, "a second close must not stop what the first already stopped"
