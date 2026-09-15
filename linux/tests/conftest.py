"""Every device is tested against a fake link; nothing here touches /dev."""

from __future__ import annotations

import itertools

import pytest

import flyball_linux.configs  # ruff: ignore[unused-import]  registers every tag

_counter = itertools.count()


@pytest.fixture
def fresh():
    """A source name no other test has used; sources register process-wide."""
    return lambda stem: f"{stem}_{next(_counter)}"
