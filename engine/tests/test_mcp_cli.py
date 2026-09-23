"""`flyball-mcp`'s own entry point: `--help` on a bare install, and a missing `mcp` extra."""

from __future__ import annotations

import sys

import pytest

from flyball.interfaces.mcp.server import main, parser


def test_help_needs_no_optional_dependency(capsys):
    """`--help` must work even where `mcp`/`anyio`/`httpx` are not installed.

    Every import `build`/`main` need is deferred past `parser().parse_args`,
    so importing this module and asking for `--help` -- which argparse
    itself handles by raising `SystemExit(0)` -- never touches them.
    """
    with pytest.raises(SystemExit) as excinfo:
        parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    assert "flyball-mcp" in capsys.readouterr().out


def test_a_missing_mcp_extra_is_one_line_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(SystemExit) as excinfo:
        main(["--url", "http://127.0.0.1:1"])
    assert excinfo.value.code == 2
    assert capsys.readouterr().err == (
        "flyball-mcp: needs the mcp extra -- pip install 'flyball[mcp]' (missing: mcp)\n"
    )
