"""PeriodicLoop: the error a run's function last raised, logged and exposed."""

from __future__ import annotations

import logging

from flyball.foundation.time.loop import PeriodicLoop


def test_starts_with_no_error():
    loop = PeriodicLoop(lambda: None, 1.0, False)
    assert loop.erroring is None


def test_set_error_exposes_it_and_logs(caplog):
    loop = PeriodicLoop(lambda: None, 1.0, False)
    boom = ValueError("boom")
    with caplog.at_level(logging.ERROR, logger="flyball.loop"):
        loop.set_error(boom)
    assert loop.erroring is boom
    assert "boom" in caplog.text


def test_set_ok_clears_it():
    loop = PeriodicLoop(lambda: None, 1.0, False)
    loop.set_error(ValueError("boom"))
    loop.set_ok()
    assert loop.erroring is None


def test__once_records_a_raised_error(caplog):
    def failing():
        raise RuntimeError("nope")

    loop = PeriodicLoop(failing, 1.0, False)
    with caplog.at_level(logging.ERROR, logger="flyball.loop"):
        loop._once()
    assert isinstance(loop.erroring, RuntimeError)
    assert "nope" in caplog.text
