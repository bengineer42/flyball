"""Strict loading (a repeated key is refused, not silently overwritten) and canonical dumping."""

from __future__ import annotations

import pytest

from flyball.foundation.files import atomic_write_text, dumps, loads


def test_duplicate_yaml_key_names_the_key_and_the_line():
    with pytest.raises(ValueError, match="'a'") as info:
        loads("a: 1\nb: 2\na: 3\n", ".yaml")
    assert "line 3" in str(info.value)


def test_duplicate_yaml_key_is_caught_when_nested():
    with pytest.raises(ValueError, match="'x'"):
        loads("outer:\n  x: 1\n  y: 2\n  x: 3\n", ".yaml")


def test_a_valid_yaml_file_still_loads():
    assert loads("a: 1\nb: {c: 2, d: 3}\n", ".yaml") == {"a": 1, "b": {"c": 2, "d": 3}}


def test_duplicate_json_key_names_the_key():
    with pytest.raises(ValueError, match="'a'"):
        loads('{"a": 1, "b": 2, "a": 3}', ".json")


def test_duplicate_json_key_is_caught_when_nested():
    with pytest.raises(ValueError, match="'x'"):
        loads('{"outer": {"x": 1, "x": 2}}', ".json")


def test_a_valid_json_file_still_loads():
    assert loads('{"a": 1, "b": {"c": 2}}', ".json") == {"a": 1, "b": {"c": 2}}


def test_duplicate_toml_key_is_refused_too():
    """TOML already refuses this on its own; only checking it stays a `ValueError`."""
    with pytest.raises(ValueError):
        loads("a = 1\na = 2\n", ".toml")


def test_a_valid_toml_file_still_loads():
    assert loads("a = 1\n[b]\nc = 2\n", ".toml") == {"a": 1, "b": {"c": 2}}


def test_dumps_yaml_is_block_style_in_the_given_key_order():
    text = dumps({"z": 1, "a": {"y": 2, "x": 3}}, ".yaml")
    assert text == "z: 1\na:\n  y: 2\n  x: 3\n"


def test_dumps_yaml_keeps_unicode_readable():
    assert "°C" in dumps({"unit": "°C"}, ".yaml")


def test_dumps_json_is_indented():
    assert dumps({"a": 1}, ".json") == '{\n  "a": 1\n}\n'


def test_dumps_toml_round_trips_through_loads():
    document = {"a": 1, "b": {"c": 2, "d": [1, 2]}}
    assert loads(dumps(document, ".toml"), ".toml") == document


def test_dumps_unknown_suffix_raises():
    with pytest.raises(ValueError, match="unknown"):
        dumps({}, ".ini")


def test_atomic_write_keeps_an_existing_files_mode(tmp_path):
    import os
    import stat
    import sys

    target = tmp_path / "sim.yaml"
    target.write_text("old\n", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(target, 0o640)
    atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["sim.yaml"], "no temp file left"


def test_atomic_write_replaces_where_a_descriptor_cannot_be_chmodded(tmp_path, monkeypatch):
    """Python < 3.13 on Windows: `os.chmod` takes no descriptor; the write still happens."""
    import os

    real = os.chmod

    def chmod(path, mode, **kw):
        if isinstance(path, int):
            raise TypeError("chmod: path should be string, bytes or os.PathLike, not int")
        return real(path, mode, **kw)

    monkeypatch.setattr(os, "chmod", chmod)
    monkeypatch.setattr(os, "supports_fd", os.supports_fd - {real})
    target = tmp_path / "sim.yaml"
    target.write_text("old\n", encoding="utf-8")
    atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"


def test_a_document_is_read_as_utf8_whatever_the_locale(tmp_path):
    """A rig file's `°C` is read as written under a non-UTF-8 locale (Windows' cp1252, or C).

    Played in a child with the C locale and no UTF-8 mode, so the default encoding is ASCII.
    """
    import os
    import subprocess
    import sys

    rig = tmp_path / "rig.yaml"
    rig.write_text("name: oven\nunit: °C\n", encoding="utf-8")
    env = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    code = (
        "import locale, sys\n"
        "from flyball.foundation.files import load_document\n"
        "print(locale.getpreferredencoding(False))\n"
        "print(ascii(load_document(sys.argv[1])['unit']))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(rig)], env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    encoding, unit = done.stdout.split()
    if encoding.lower().replace("-", "") == "utf8":
        pytest.skip("this platform's C locale is UTF-8 already")
    assert unit == ascii("°C")
