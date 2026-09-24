"""CI enforcement: every installed `flyball.configs` entry point actually registers something.

Guards against the bluesky/qcodes/pymeasure regression, where installing a
package was not the same as importing it, and importing was not the same as
registering. An entry point missing a `register(catalog)` function, or one
that runs and silently registers nothing, is otherwise invisible until a rig
file needs a tag it should have provided. An ordinary test in the suite,
so `make test`/CI already runs it -- not a separate CI-level script.
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
