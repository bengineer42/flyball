"""CI enforcement: every installed `flyball.configs` entry point actually registers something.

Guards against the bluesky/qcodes/pymeasure regression, where installing a
package was not the same as importing it, and importing was not the same as
registering. An entry point missing a `register(catalog)` function, or one
that runs and silently registers nothing, is otherwise invisible until a rig
file needs a tag it should have provided. An ordinary test in the suite,
so `make test`/CI already runs it -- not a separate CI-level script.
"""

from __future__ import annotations

import importlib.metadata
import logging
import sys
from importlib.metadata import EntryPoint, entry_points
from types import ModuleType

import pytest

from flyball.model.catalog import Catalogs


def _registered(catalogs: Catalogs) -> int:
    return (
        len(catalogs.devices)
        + len(catalogs.links)
        + len(catalogs.laws)
        + len(catalogs.feedforwards)
        + len(catalogs.generators)
        + len(catalogs.steps)
    )


def test_every_installed_flyball_configs_entry_point_registers_something() -> None:
    entries = list(entry_points(group="flyball.configs"))
    assert entries, "no `flyball.configs` entry points are installed here -- nothing to check"
    for entry in entries:
        module = entry.load()
        register = getattr(module, "register", None)
        assert callable(register), (
            f"{entry.name!r} ({entry.value}) has no register(catalog) function -- installing it"
            " registers nothing at all (the bluesky/qcodes/pymeasure regression)"
        )
        catalog = Catalogs()
        register(catalog)
        assert _registered(catalog) > 0, (
            f"{entry.name!r} ({entry.value}).register() ran but registered nothing"
        )


def test_discover_populates_the_kinds_engine_itself_ships() -> None:
    """`Catalogs().discover()` end to end: what `runner.py` does once at startup.

    Laws, feedforwards and generators are all engine-only today (no
    extension ships its own), so this also confirms `control/configs.py`
    actually registers each of the three kinds, not just laws.
    """
    catalogs = Catalogs()
    catalogs.discover()
    assert catalogs.discovery_errors == {}, "an installed entry point failed to load"
    assert catalogs.laws.names(), "engine's own built-in laws (control/configs.py) did not load"
    assert set(catalogs.laws.names()) >= {
        "open_loop",
        "p",
        "pi",
        "pid",
        "imc",
        "on_off",
        "smith",
        "scheduled",
        "sliding",
    }, "one of engine's 9 built-in laws is missing"
    assert set(catalogs.feedforwards.names()) >= {"identity", "none", "affine", "table"}, (
        "engine's own built-in feedforwards (control/configs.py) did not all load"
    )
    assert set(catalogs.generators.names()) >= {"dwell", "linear_ramp_setpoint", "profile"}, (
        "engine's own built-in generators (control/configs.py) did not all load"
    )
    assert set(catalogs.steps.names()) >= {
        "prompt",
        "set",
        "run",
        "regulate",
        "ramp",
        "wait",
        "settle",
        "manual",
    }, "engine's own built-in commands (sequencing/configs.py) did not all load"


class _Fake:
    """A stand-in class for the fake packages below to register."""


def _module(monkeypatch: pytest.MonkeyPatch, name: str, register) -> None:
    module = ModuleType(name)
    module.register = register  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, name, module)


def test_discover_skips_and_reports_a_broken_or_clashing_entry_point(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One bad package is logged and left out whole; every other one still loads."""
    real = list(entry_points(group="flyball.configs"))
    reference = Catalogs()
    reference.discover()
    taken = next(iter(reference.links))  # a link type an installed package already registers

    def clashing(catalog: Catalogs) -> None:
        catalog.register_device(_Fake, name="fake_before_the_clash")
        catalog.register_link(_Fake, name=taken)

    def good(catalog: Catalogs) -> None:
        catalog.register_device(_Fake, name="fake_good")

    _module(monkeypatch, "fake_flyball_clash", clashing)
    _module(monkeypatch, "fake_flyball_good", good)
    fakes = [
        EntryPoint("broken", "fake_flyball_no_such_module", "flyball.configs"),
        EntryPoint("clash", "fake_flyball_clash", "flyball.configs"),
        EntryPoint("good", "fake_flyball_good", "flyball.configs"),
    ]
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group: [*real, *fakes])

    catalogs = Catalogs()
    with caplog.at_level(logging.ERROR, logger="flyball.model.catalog"):
        loaded = catalogs.discover()

    assert loaded == [*(e.name for e in real), "good"]
    assert set(catalogs.discovery_errors) == {"broken", "clash"}
    assert catalogs.discovery_errors["broken"].startswith("ModuleNotFoundError: ")
    assert catalogs.discovery_errors["clash"] == (
        f"ValueError: link type {taken!r} is already {reference.links[taken].__name__}"
    )
    # the clashing package is left out whole: what it registered before the clash is gone
    assert "fake_before_the_clash" not in catalogs.devices
    assert catalogs.links[taken] is reference.links[taken]
    assert "fake_good" in catalogs.devices
    assert set(reference.devices) <= set(catalogs.devices)
    logged = caplog.text
    assert "'broken' (fake_flyball_no_such_module) skipped" in logged
    assert "'clash' (fake_flyball_clash) skipped" in logged
    assert taken in logged

    # a type the skipped package would have provided is the usual unknown type
    with pytest.raises(KeyError, match="device type 'fake_before_the_clash' is not registered"):
        catalogs.devices["fake_before_the_clash"]


def test_discover_again_clears_an_entry_that_now_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def flaky(catalog: Catalogs) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("not yet")
        catalog.register_device(_Fake, name="fake_flaky")

    _module(monkeypatch, "fake_flyball_flaky", flaky)
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group: [EntryPoint("flaky", "fake_flyball_flaky", "flyball.configs")],
    )
    catalogs = Catalogs()
    assert catalogs.discover() == []
    assert catalogs.discovery_errors == {"flaky": "RuntimeError: not yet"}
    assert catalogs.discover() == ["flaky"]
    assert catalogs.discovery_errors == {}
    assert "fake_flaky" in catalogs.devices
