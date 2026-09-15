"""`flyball new`: the generated module imports, registers its tag and builds a working device."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from flyball.core.config import Config
from flyball.scaffold import render, write


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_actuator_template_builds_and_takes_a_demand(tmp_path, fresh):
    name = fresh("scaffold_heater")
    module = load(write("actuator", name, tmp_path))
    config = Config.registry[name]()
    device = config.build()
    device.set_demand(150.0)
    assert device.state.demand == 150.0 and device.state.output == 1.0
    assert device.set_limits(0.0, 200.0).limits == (0.0, 200.0)
    assert "set_limits" in type(device).commands
    assert type(device).__name__ == "".join(p.capitalize() for p in name.split("_"))
    assert module.__doc__.startswith(type(device).__name__)


def test_reader_template_builds_and_reads(tmp_path, fresh):
    name = fresh("scaffold_probe")
    load(write("reader", name, tmp_path))
    device = Config.registry[name]().build()
    (sample,) = device.read(5)
    assert sample.time_ns == 5 and sample.source.name == name
    assert device.state.last == 0.0


def test_names_are_made_safe_and_files_are_not_overwritten(tmp_path):
    assert 'tag="lab_probe"' in render("reader", "Lab-Probe 2".replace(" 2", ""))
    for bad in ("", "2fast", "class"):
        with pytest.raises(ValueError):
            render("actuator", bad)
    with pytest.raises(ValueError, match="no template"):
        render("loop", "x")
    (tmp_path / "taken.py").write_text("")
    with pytest.raises(FileExistsError):
        write("actuator", "taken", tmp_path)
