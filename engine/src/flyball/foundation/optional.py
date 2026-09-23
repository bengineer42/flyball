"""Checking for an optional dependency an entry point needs, with one clear line if it is missing.

`flyball-runner` and `flyball-mcp` both ship on a bare `pip install flyball`
(`[project.scripts]`), but each needs one of the optional extras to actually
run. Left unchecked, the first missing import surfaces as a raw traceback or
a bare `No module named 'x'` deep in some unrelated call. `require` instead
names the extra to install, once, before any of that work starts.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence


class MissingExtra(SystemExit):
    """Raised (as a `SystemExit`) when a top-level module an entry point needs is not installed.

    Carries `module` and `extra` so a caller that wants to handle this itself
    (rather than let it propagate as an exit) can catch it and read them.
    """

    def __init__(self, program: str, extra: str, module: str) -> None:
        self.program = program
        self.extra = extra
        self.module = module
        super().__init__(2)


def require(program: str, extra: str, modules: Sequence[str]) -> None:
    """Import each of `modules` in order; on the first one missing, print one line and exit(2).

    A `ModuleNotFoundError` is only treated as "not installed" when its
    `.name` is the module asked for (or a package it lives under) -- an
    import that fails *inside* an installed module, because one of its own
    dependencies is broken, is a real bug and is left to propagate as a
    traceback, not swallowed as if the extra were missing.

    Args:
        program: The console script's own name, for the printed line
            (`flyball-runner`, `flyball-mcp`).
        extra: The `[project.optional-dependencies]` extra that provides
            `modules`, e.g. `"server"`.
        modules: Top-level module names to probe, in order. The first one
            missing is reported; the rest are not tried.

    Raises:
        MissingExtra: A `SystemExit(2)`, after printing one line to stderr:
            `f"{program}: needs the {extra} extra -- pip install 'flyball[{extra}]'"`
            `f" (missing: {module})"`.
    """
    for module in modules:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as e:
            if e.name != module and not (e.name and module.startswith(e.name + ".")):
                raise  # a broken import inside an installed package, not a missing one
            print(
                f"{program}: needs the {extra} extra -- pip install 'flyball[{extra}]'"
                f" (missing: {module})",
                file=sys.stderr,
            )
            raise MissingExtra(program, extra, module) from None
