"""Fixtures shared by the suite.

Sources, measurands, commands and base dimensions register process-wide by
name, so every test that declares one uses a name unique to that test
(``fresh``) and the command registry is restored after each test.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator

import pytest

from flyball.core.reading import Measurand, Source
from flyball.core.units.si import Celsius
from flyball.programmer.command import Commands
from flyball.runtime.rig import Rig
from flyball.sim import RecordingActuator, SteppedClock
from helpers import DutyHeater

_counter = itertools.count()


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
def temperature(fresh: Callable[[str], str]) -> Measurand:
    return Measurand(fresh("temperature"), Celsius, range=(-40.0, 125.0), precision=2)


@pytest.fixture
def probe(fresh: Callable[[str], str], temperature: Measurand) -> Source:
    return Source(fresh("probe"), (temperature,))


@pytest.fixture
def clock() -> SteppedClock:
    return SteppedClock(0)


@pytest.fixture
def rig(clock: SteppedClock) -> Rig:
    rig = Rig()
    rig.clock = clock
    return rig


@pytest.fixture
def heater(fresh: Callable[[str], str]) -> RecordingActuator:
    return RecordingActuator(fresh("heater"))


@pytest.fixture
def duty_heater(fresh: Callable[[str], str]) -> DutyHeater:
    return DutyHeater(fresh("heater"))
