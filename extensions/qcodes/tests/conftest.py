"""Fixtures shared by the suite: a fresh name per test."""

from __future__ import annotations

import itertools
from collections.abc import Callable

import flyball_modbus  # ruff: ignore[unused-import]  registers the modbus link tag
import flyball_visa  # ruff: ignore[unused-import]  registers the visa link tag
import pytest

_counter = itertools.count()


@pytest.fixture
def fresh() -> Callable[[str], str]:
    """A name no other test has used: ``fresh("smu")`` -> ``smu_17``."""
    return lambda stem: f"{stem}_{next(_counter)}"
