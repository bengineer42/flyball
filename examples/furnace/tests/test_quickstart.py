"""The book's quick start runs this rig with `flyball-runner`: the server stack must be here."""

from __future__ import annotations

import importlib


def test_the_runner_can_serve():
    # `flyball-runner` imports uvicorn only once it starts serving; a plain `flyball` dependency
    # passes every other test here and then dies on `uv run flyball-runner rig.yaml`.
    for module in ("uvicorn", "fastapi", "flyball.interfaces.server"):
        importlib.import_module(module)
