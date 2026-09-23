"""One-off: rewrite a store's programs for the step renames (C9) and nested timeouts (C14).

    uv run python scripts/rewrite_programs_c9.py RIG.sqlite [--dry-run]

Not a migration, and not shipped as one: run it once against each store that
holds programs written before the renames. Programs are versioned bodies with
a sha256, so nothing is rewritten in place -- each affected program gets a
*new* version (labelled `c9 rewrite`, its hash computed from the new body) and
its old versions stay in its history.

What changes, in a program written with the old names:

- the timed step `hold:` becomes `wait:`, its flat time keys written inside
  `duration:` when it has a `message` (`wait: {duration: {minutes: 5},
  message: soak}`), since flat keys beside a message read as an old prompt;
- the operator step `wait:` becomes `prompt:`;
- `arrive:` becomes `settle:`, and its `readings:` becomes `count:`;
- a flat time key on a `prompt` or `settle` (`arrive: {minutes: 10}`), which
  used to fold into its `timeout`, is written inside it:
  `settle: {timeout: {minutes: 10}}`.

A program counts as old if it has a `hold:` or `arrive:` step, or a `wait:`
that the new loader refuses as an operator prompt (a bare message, a `name`,
or a `message` with no `duration`). In an old program every `wait:` is the
operator step. A program that already loads under the new names is left
alone, so running the script twice saves nothing the second time.

The new version is dumped from the parsed document, so a YAML program's
comments do not survive into it (they stay in the old version). A rewrite
that still does not load is reported and not saved; the loader's targeted
errors say what is left to fix by hand.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from flyball.foundation.time import DURATION_KEYS
from flyball.interfaces.server.dialect import (
    Dialect,
    StepError,
    check_renamed,
    program_from_document,
)
from flyball.interfaces.server.formats import dump, parse
from flyball.model.catalog import ensure_discovered
from flyball.record.sqlite import SqliteStore

LABEL = "c9 rewrite"


def _is_old(steps: list[Any], commands: Mapping[str, Any]) -> bool:
    """Whether `steps` were written with the names from before the renames."""
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        for key, body in step.items():
            try:
                check_renamed(key, body, f"step {index}", commands)
            except StepError:
                return True
    return False


def _nest_timeout(body: dict[str, Any]) -> dict[str, Any]:
    """Flat time keys -> `timeout: {...}`, for a step whose only time field is its timeout."""
    flat = {key: body.pop(key) for key in list(body) if key in DURATION_KEYS}
    if flat and "timeout" not in body:
        body["timeout"] = flat
    elif flat:
        body.update(flat)  # both: leave it for the loader to refuse, rather than guess
    return body


def rewrite_step(step: Any) -> Any:
    """One old-format file step -> the same step under the new names."""
    if not isinstance(step, Mapping):
        return step
    out: dict[str, Any] = {}
    for key, body in step.items():
        if key == "hold":
            if isinstance(body, Mapping) and "message" in body and "duration" not in body:
                # A timed wait with a message spells out `duration:`; flat keys beside a
                # message read as an old operator prompt.
                body = dict(body)
                flat = {k: body.pop(k) for k in list(body) if k in DURATION_KEYS}
                body = {"duration": flat, **body} if flat else body
            out["wait"] = body
        elif key == "wait":
            if isinstance(body, Mapping):
                body = _nest_timeout(dict(body))
            out["prompt"] = body
        elif key in ("arrive", "settle"):
            if isinstance(body, Mapping):
                body = dict(body)
                if "readings" in body and "count" not in body:
                    body["count"] = body.pop("readings")
                body = _nest_timeout(body)
            out["settle"] = body
        else:
            out[key] = body
    return out


def rewrite_document(document: Any, commands: Mapping[str, Any]) -> Any | None:
    """The document under the new names, or None if it needs no rewrite."""
    if not isinstance(document, Mapping) or not isinstance(document.get("steps"), list):
        return None
    if not _is_old(document["steps"], commands):
        return None
    return {**document, "steps": [rewrite_step(step) for step in document["steps"]]}


def run(path: Path, dry_run: bool = False, out: Any = sys.stdout) -> int:
    """Rewrite every affected program in the store at `path`; the number of versions saved."""
    commands = dict(ensure_discovered().commands.items())
    dialect = Dialect(commands=commands)
    store = SqliteStore(path)
    saved = 0
    try:
        for row in store.programs():
            try:
                document = parse(row.body, row.format)
            except Exception as error:  # a stored program need not parse; say so and move on
                print(f"{row.name}: skipped, does not parse: {error}", file=out)
                continue
            new = rewrite_document(document, commands)
            if new is None:
                continue
            try:
                program_from_document(new, dialect)
            except Exception as error:
                print(f"{row.name}: not saved, the rewrite still does not load: {error}", file=out)
                continue
            body = dump(new, row.format)
            if dry_run:
                print(f"{row.name}: would save a new version:\n{body}", file=out)
                continue
            newest = max(r.created_ns for r in store.program_history(row.name))
            created_ns = max(time.time_ns(), newest + 1)
            version = store.save_program(
                row.name, row.format, body, created_ns, label=LABEL, notes=row.notes
            )
            print(f"{row.name}: saved version #{version.id} (was #{row.id})", file=out)
            saved += 1
    finally:
        store.close()
    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("store", type=Path, help="the rig's sqlite store")
    parser.add_argument("--dry-run", action="store_true", help="print, save nothing")
    args = parser.parse_args(argv)
    if not args.store.exists():
        parser.error(f"{args.store}: no such file")
    saved = run(args.store, dry_run=args.dry_run)
    print(f"{saved} program(s) rewritten", file=sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
