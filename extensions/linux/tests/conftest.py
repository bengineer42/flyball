"""Every device is tested against a fake link; nothing here touches /dev."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flyball.model.catalog import Catalogs, set_catalog


@pytest.fixture(scope="session", autouse=True)
def _catalog() -> Iterator[Catalogs]:
    catalog = Catalogs()
    catalog.discover()
    set_catalog(catalog)
    yield catalog
    set_catalog(None)
