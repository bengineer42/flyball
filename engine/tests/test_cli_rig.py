"""`flyball rig check`, in process: several files, `--set`, and `--print`."""

from __future__ import annotations

from flyball import cli


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def _device_toml(name: str) -> str:
    return f"""
[links.bench]
tag = "fake_text"
replies = {{}}

[devices.{name}]
driver = "scpi"
link = "bench"
channels = {{ voltage = {{ query = "MEAS:VOLT?", unit = "V" }} }}
"""


def test_rig_check_of_one_file_prints_a_summary(tmp_path, capsys):
    a = _write(tmp_path, "a.toml", _device_toml("psu"))
    assert cli.main(["rig", "check", str(a)]) == 0
    out = capsys.readouterr().out
    assert "ok" in out and "1 devices" in out


def test_rig_check_layers_two_files_and_reports_the_merge(tmp_path, capsys):
    a = _write(tmp_path, "a.toml", _device_toml("psu"))
    b = _write(tmp_path, "b.yaml", "name: layered\n")
    assert cli.main(["rig", "check", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "ok" in out and "layered" in out and "from 2 files" in out


def test_set_overrides_a_value_after_the_files_load(tmp_path, capsys):
    a = _write(tmp_path, "a.toml", _device_toml("psu"))
    assert cli.main(["rig", "check", str(a), "--set", "name=named"]) == 0
    assert "named" in capsys.readouterr().out


def test_print_shows_the_merged_document_in_the_first_file_s_format(tmp_path, capsys):
    a = _write(tmp_path, "a.toml", 'name = "a"\n')
    b = _write(tmp_path, "b.yaml", "recording: true\n")
    assert cli.main(["rig", "check", str(a), str(b), "--set", "name=final", "--print"]) == 0
    out = capsys.readouterr().out
    printed = out.split("\n", 1)[1]  # drop the summary line
    assert 'name = "final"' in printed and "recording = true" in printed


def test_a_reserved_name_is_a_message_not_a_traceback(tmp_path, capsys):
    a = _write(tmp_path, "a.toml", _device_toml("schema"))
    assert cli.main(["rig", "check", str(a)]) == 1
    assert "'schema' is reserved" in capsys.readouterr().err
