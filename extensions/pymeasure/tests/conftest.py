"""Fixtures shared by the suite: a fresh name per test, every tag discovered and registered."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator

import pytest
from flyball.model.catalog import Catalogs, set_catalog

_counter = itertools.count()


@pytest.fixture(scope="session", autouse=True)
def _catalog() -> Iterator[Catalogs]:
    catalog = Catalogs()
    catalog.discover()
    set_catalog(catalog)
    yield catalog
    set_catalog(None)


@pytest.fixture
def fresh() -> Callable[[str], str]:
    """A name no other test has used: ``fresh("smu")`` -> ``smu_17``."""
    return lambda stem: f"{stem}_{next(_counter)}"
