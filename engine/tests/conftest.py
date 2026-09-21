"""Fixtures shared by the suite.

Config tags, commands and base dimensions register process-wide by name, so
every test that declares one uses a name unique to that test (``fresh``)
and the command registry is restored after each test.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from flyball_sim.clock import SteppedClock

from flyball.model.catalog import Catalogs, set_catalog
from flyball.rig import Rig
from flyball.runtime.config import RunnerConfig
from flyball.sequencing.command import Commands

_counter = itertools.count()


@pytest.fixture(scope="session", autouse=True)
def _catalog() -> Iterator[Catalogs]:
    """`Catalogs().discover()`, set as the current one for the whole session.

    What `runner.py` does once at startup, in production; here it stands in
    for that so the suite's own sim/link/driver tags (``sim_daq``, ...) are
    registered the same way an installed package's are, not by import side
    effect. A test that needs an isolated catalog builds its own and passes
    it explicitly rather than mutating this one.
    """
    catalog = Catalogs()
    catalog.discover()
    set_catalog(catalog)
    yield catalog
    set_catalog(None)


@pytest.fixture
def fresh() -> Callable[[str], str]:
    """A name no other test has used: ``fresh("probe")`` -> ``probe_17``."""
    return lambda stem: f"{stem}_{next(_counter)}"


@pytest.fixture(autouse=True)
def _restore_commands() -> Iterator[None]:
    before = dict(Commands)
    yield
    Commands.clear()
    Commands.update(before)


@pytest.fixture
def clock() -> SteppedClock:
    return SteppedClock(0)


@pytest.fixture
def rig(clock: SteppedClock) -> Rig:
    rig = Rig()
    rig.clock = clock
    return rig


class FakeRunner:
    """A stand-in for `flyball.runner.Handle`: what `set_runner` takes; remembers what was asked."""

    def __init__(self, settings: RunnerConfig | None = None, files: list[Path] | None = None):
        self.settings = settings or RunnerConfig()
        self.files = files or []
        self.asked: list[str] = []

    def shutdown(self) -> None:
        self.asked.append("shutdown")

    def restart(self) -> None:
        self.asked.append("restart")
