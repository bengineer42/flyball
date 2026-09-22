"""`flyball new`: the generated module imports, registers its tag and builds a working device."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from flyball.foundation.config import Config
from flyball.scaffold import render, write


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_device_template_builds_reads_and_takes_a_command(tmp_path, fresh):
    name = fresh("scaffold_probe")
    module = load(write(name, tmp_path))
    (cls,) = (
        v
        for v in vars(module).values()
        if isinstance(v, type) and issubclass(v, Config) and v.config_tag == name
    )
    config = cls()
    device = config.build(name)
    (sample,) = device.read(5)
    assert sample.time_ns == 5 and sample.node is device.root
    assert sample.values == {device.signals["value"]: 0.0}
    assert device.reset() is None
    assert device.signals["value"].value == 0.0, "reset pushed the output"
    assert type(device).__name__ == "".join(p.capitalize() for p in name.split("_"))
    assert module.__doc__.startswith(type(device).__name__)
    assert "reset" in type(device).commands


def test_names_are_made_safe_and_files_are_not_overwritten(tmp_path):
    assert 'tag="lab_probe"' in render("Lab-Probe 2".replace(" 2", ""))
    for bad in ("", "2fast", "class"):
        with pytest.raises(ValueError):
            render(bad)
    (tmp_path / "taken.py").write_text("")
    with pytest.raises(FileExistsError):
        write("taken", tmp_path)
