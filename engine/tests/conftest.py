"""Fixtures shared by the suite.

Config tags and base dimensions register process-wide by name, so every test
that declares one uses a name unique to that test (``fresh``).
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient as _StarletteClient
from flyball_sim.clock import SteppedClock

import flyball.record.sqlite
from flyball.model.catalog import Catalogs, set_catalog
from flyball.rig import Rig
from flyball.runtime.config import RunnerConfig

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


class _LoopGuardedLock:
    """The store's `RLock`, noting every acquisition made on a thread running an event loop.

    The server's loop must never wait for the store (`deps.py`); a TestClient
    runs the app's loop on a thread of its own, so an acquisition there is a
    route or task calling the store on the loop. The main thread is left out:
    an `async def` test may call the store itself.
    """

    def __init__(self, seen: list[str]) -> None:
        self._inner = threading.RLock()
        self._seen = seen

    def acquire(self, *args: Any, **kwargs: Any) -> bool:
        if threading.current_thread() is not threading.main_thread():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._seen.append("".join(traceback.format_stack(limit=12)))
        return self._inner.acquire(*args, **kwargs)

    def release(self) -> None:
        self._inner.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


@pytest.fixture(autouse=True)
def _store_off_the_loop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail any test in which the app's event loop took the store's lock."""
    seen: list[str] = []
    monkeypatch.setattr(flyball.record.sqlite, "RLock", lambda: _LoopGuardedLock(seen))
    yield
    assert not seen, "the store was called on the event loop:\n" + seen[0]


@pytest.fixture
def fresh() -> Callable[[str], str]:
    """A name no other test has used: ``fresh("probe")`` -> ``probe_17``."""
    return lambda stem: f"{stem}_{next(_counter)}"


@pytest.fixture
def clock() -> SteppedClock:
    return SteppedClock(0)


@pytest.fixture
def rig(clock: SteppedClock) -> Rig:
    rig = Rig()
    rig.clock = clock
    return rig


class TestClient(_StarletteClient):
    """The test client as a browser on the runner's own machine: `localhost`, not `testserver`.

    An open runner answers only loopback names (`interfaces/server/auth.py`), and
    Starlette's client sends `testserver` -- for its sockets whatever `base_url` says.
    """

    __test__ = False  # not a test class, whatever its name

    def __init__(self, app: Any, base_url: str = "http://localhost", **kwargs: Any) -> None:
        super().__init__(app, base_url=base_url, **kwargs)

    def websocket_connect(self, url: str, subprotocols: Any = None, **kwargs: Any) -> Any:
        base = str(self.base_url).replace("http", "ws", 1)
        return super().websocket_connect(urljoin(base, url), subprotocols, **kwargs)


class FakeRunner:
    """A stand-in for `flyball.runner.Handle`: what `set_runner` takes; remembers what was asked."""

    def __init__(self, settings: RunnerConfig | None = None, files: list[Path] | None = None):
        self.settings = settings or RunnerConfig()
        self.files = files or []
        self.asked: list[str] = []
        self.exposure = None

    def shutdown(self) -> None:
        self.asked.append("shutdown")

    def restart(self) -> None:
        self.asked.append("restart")
