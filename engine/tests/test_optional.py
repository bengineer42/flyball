"""`require`: one clear line and exit(2) when an entry point's extra is missing."""

from __future__ import annotations

import importlib
import sys

import pytest

from flyball.foundation.optional import MissingExtra, require


def test_a_missing_top_level_module_prints_one_line_and_exits_2(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "definitely_not_installed", None)
    with pytest.raises(MissingExtra) as excinfo:
        require("flyball-runner", "server", ["definitely_not_installed"])
    assert excinfo.value.code == 2
    assert excinfo.value.program == "flyball-runner"
    assert excinfo.value.extra == "server"
    assert excinfo.value.module == "definitely_not_installed"
    assert capsys.readouterr().err == (
        "flyball-runner: needs the server extra -- pip install 'flyball[server]'"
        " (missing: definitely_not_installed)\n"
    )


def test_only_the_first_missing_module_is_reported(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "first_missing_dep", None)
    monkeypatch.setitem(sys.modules, "second_missing_dep", None)
    with pytest.raises(MissingExtra):
        require("prog", "extra", ["first_missing_dep", "second_missing_dep"])
    err = capsys.readouterr().err
    assert "first_missing_dep" in err
    assert "second_missing_dep" not in err


def test_every_module_present_needs_no_report():
    require("prog", "extra", ["json", "os"])  # stdlib: always there, nothing printed or raised


def test_a_broken_import_inside_an_installed_module_is_not_swallowed(monkeypatch):
    """A `ModuleNotFoundError` for something other than the module asked for is a real bug."""

    def fake_import(name: str, *a: object, **k: object) -> object:
        raise ModuleNotFoundError(
            "No module named 'its_own_missing_dep'", name="its_own_missing_dep"
        )

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(ModuleNotFoundError) as excinfo:
        require("prog", "extra", ["installed_but_broken"])
    assert excinfo.value.name == "its_own_missing_dep"
