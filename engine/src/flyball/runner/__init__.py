"""Serve a rig described by a file.

    flyball-runner rig.toml
    FLYBALL_TOKEN=... flyball-runner rig.toml --host 0.0.0.0 --port 8000 --record
    flyball-runner furnace.yaml sim.yaml --set clock.speed=60

Builds the rig from the file (any of `.toml`, `.yaml`, `.json`), starts its
devices polling, optionally opens a recording session, and serves the HTTP
and websocket API until stopped. Several files layer, later overlaying earlier
(see [flyball.runtime.overlay][]); the store and the programs directory then
default off the first one. An application with hardware the rig file cannot
describe writes its own runner around [serve][flyball.runner.serving.serve].

Split by concern: `cli` (the argument parser and `settle`, turning flags + a
rig file's `runner:` section into one
[RunnerConfig][flyball.runtime.config.RunnerConfig]), `starting` (building the
rig and its store, session/version bookkeeping), `serving` (the process
handle and the actual serving), `entrypoint` (`main`, wiring the three
together). None of the submodules share a name with a symbol it defines (`serving.py`,
not `serve.py`; `starting.py`, not `start.py`; `entrypoint.py`, not `main.py`)
-- a module and a same-named member of it both resolving from the same
dotted path breaks both `pytest.monkeypatch.setattr("flyball.runner.serve.serve", ...)`
style patching and mkdocstrings' cross-references, so the two never collide
here.
"""

from __future__ import annotations

from .cli import parser, settle
from .entrypoint import main
from .serving import Handle, serve
from .starting import START_REASONS, keep_versions, resumed, start, start_with_store

__all__ = [
    "START_REASONS",
    "Handle",
    "keep_versions",
    "main",
    "parser",
    "resumed",
    "serve",
    "settle",
    "start",
    "start_with_store",
]
