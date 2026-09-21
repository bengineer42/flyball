"""CI enforcement: every installed `flyball.configs` entry point actually registers something.

Prompted by `brain/tasks/registry-redesign.md`'s root cause: the
bluesky/qcodes/pymeasure regression, where installing a package was not the
same as importing it, and importing was not the same as registering. An
entry point missing a `register(catalog)` function, or one that runs and
silently registers nothing, is otherwise invisible until a rig file needs a
tag it should have provided. This is the "CI-level script" option
`registry-redesign.md`'s own open item 1 picked as the only one that
actually prevents a repeat, not just makes it easier to test for -- an
ordinary test in the suite `make test`/CI already runs.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from flyball.model.catalog import Catalogs


def _registered(catalogs: Catalogs) -> int:
    return (
        len(catalogs.devices)
        + len(catalogs.links)
        + len(catalogs.laws)
        + len(catalogs.feedforwards)
        + len(catalogs.generators)
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
    """`Catalogs().discover()` end to end: what `runner.py` does once at startup."""
    catalogs = Catalogs()
    catalogs.discover()
    assert catalogs.laws.tags(), "engine's own built-in laws (control/configs.py) did not load"
