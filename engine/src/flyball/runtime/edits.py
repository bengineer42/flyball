"""A rig edit, saved where the next start finds it (D-051).

An edit to a running rig is not applied in place: it is saved, the rig is stopped, and the
runner starts again from what was saved. What is saved depends on where the rig came from
([Origin][flyball.runtime.edits.Origin]):

- **From rig files:** the runner's own overlay beside the first file, `<file>.d/added.<suffix>`,
  which every start loads after the files and before `--set`. It holds the difference between
  the edited rig and what the files (and the other overlays, and the `--set` values) build
  without it, so a later change to the file itself still takes effect where the edit did not
  touch it. The overlay it replaces is kept as `added.<suffix>.prev`, for a rollback.
- **With no rig file, or resumed (`--resume`):** the store alone. The edit is a rig version
  and the runner starts again with `--resume`, which builds from it.

Either way the edit is also a row in the store's rig versions, and the head: the overlay is
checked, before it is written, to give exactly that version's document, so the two never
disagree.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flyball.foundation.files import dumps_without_none, load_document
from flyball.runtime.config import (
    RigConfig,
    document_of,
    resolve_with_overlay,
    saved_overlay_path,
)
from flyball.runtime.overlay import delta, parse_set

__all__ = [
    "NotSaveable",
    "Origin",
    "Plan",
    "plan",
    "roll_back",
    "write",
]

log = logging.getLogger("flyball.runner")


class NotSaveable(Exception):
    """The edit is valid but cannot be saved where the next start would find it.

    A key a `--set` pins, a change the overlay cannot express (a board's link removed: the
    board adds it back), a `.d/` overlay after `added.*` that sets the same key. Nothing
    was written.
    """


@dataclass(frozen=True)
class Origin:
    """Where a runner's rig came from: what a start with the same command line would load."""

    layers: tuple[Path, ...] = ()
    """The rig files the command line named, in order; empty for a bare runner."""
    sets: tuple[str, ...] = ()
    """The command line's `--set KEY=VALUE`s, applied last at every start."""
    resumed: bool = False
    """Started with `--resume`: built from the store's head, whatever the files say."""

    @property
    def stored(self) -> bool:
        """Whether an edit lives in the store alone: a bare or a resumed runner."""
        return self.resumed or not self.layers

    @property
    def overlay(self) -> Path | None:
        """Where an edit is saved; None for a runner whose edits live in the store alone."""
        return None if self.stored else saved_overlay_path(self.layers[0])


@dataclass(frozen=True)
class Plan:
    """What saving one edit writes: the overlay's document (empty: none) and where."""

    document: dict[str, Any]
    """The edited rig, as `Rig.document()` will render it once the runner has started again."""
    overlay: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None
    """Where `overlay` goes; None for a runner whose edits live in the store alone."""


def plan(origin: Origin, target: dict[str, Any]) -> Plan:
    """Validate `target` and work out what saving it writes; nothing is written here.

    Raises:
        ValueError: `target` is not a valid rig (the model's message).
        NotSaveable: It is valid, but a start from `origin` cannot be made to build it.
    """
    document = document_of(RigConfig.model_validate(target))
    path = origin.overlay
    if path is None:
        return Plan(document)
    base = _rendered(origin, {})
    for expr in origin.sets:
        keys, _ = parse_set(expr)
        if _at(base, keys) != _at(document, keys):
            raise NotSaveable(
                f"{'.'.join(keys)} is pinned by the runner's --set {expr!r}, which every start"
                " applies last: change it on the command line"
            )
    overlay = delta(base, document)
    got = _rendered(origin, overlay)
    if got != document:
        keys = sorted(_paths(delta(got, document)))
        raise NotSaveable(
            f"the rig files would not build this edit from {path.name}: "
            f"{', '.join(keys[:5])}{' ...' if len(keys) > 5 else ''} would come out"
            " differently (a board's link, or a later overlay in"
            f" {path.parent.name}/, sets it); change the rig file instead"
        )
    return Plan(document, overlay, path)


def write(plan: Plan) -> None:
    """Write `plan`'s overlay, keeping the one it replaces as `.prev`; no overlay, no file.

    Raises:
        OSError: It could not be written; the overlay that was there is still there.
    """
    path = plan.path
    if path is None:
        return
    previous = _prev(path)
    if path.exists():
        previous.write_bytes(path.read_bytes())
    elif previous.exists():
        previous.unlink()  # a rollback deletes the overlay: there was none before
    if not plan.overlay:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(exist_ok=True)
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(dumps_without_none(plan.overlay, path.suffix))
    os.replace(partial, path)


def roll_back(origin: Origin) -> bool:
    """Put back the overlay the last edit replaced; whether there was one to change.

    `.prev` absent: there was no overlay before that edit, so the edit's is removed.
    """
    path = origin.overlay
    if path is None:
        return False
    previous = _prev(path)
    if previous.exists():
        os.replace(previous, path)
        return True
    if path.exists():
        path.unlink()
        return True
    return False


def overrides(overlay: Path) -> list[str]:
    """The keys a saved overlay sets or removes, two deep (`devices.probe`, `links.t1`)."""
    document = load_document(overlay)
    if not isinstance(document, dict):
        return []
    return sorted(_paths(document, depth=2))


def newer_files(overlay: Path, files: Sequence[Path]) -> list[str]:
    """The rig files changed after `overlay` was saved that set a key it sets too.

    A saved overlay wins over the files; a hand edit made to the same key afterwards is
    therefore not what the rig runs, and the runner says so at start.
    """
    try:
        saved_at = overlay.stat().st_mtime
    except OSError:
        return []
    keys = set(overrides(overlay))
    stale: list[str] = []
    for path in files:
        if path.resolve() == overlay.resolve() or path.parent == overlay.parent:
            continue
        try:
            if path.stat().st_mtime <= saved_at:
                continue
            document = load_document(path)
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and (both := keys & set(_paths(document, depth=2))):
            stale.append(f"{path} ({', '.join(sorted(both))})")
    return stale


def _rendered(origin: Origin, overlay: dict[str, Any]) -> dict[str, Any]:
    document, _ = resolve_with_overlay(origin.layers, origin.sets, overlay)
    return document_of(RigConfig.model_validate(document))


def _prev(path: Path) -> Path:
    return path.with_name(path.name + ".prev")


def _at(document: Any, keys: list[str]) -> Any:
    for key in keys:
        if not isinstance(document, dict) or key not in document:
            return None
        document = document[key]
    return document


def _paths(document: dict[str, Any], depth: int = 3, prefix: str = "") -> list[str]:
    out: list[str] = []
    for key, value in document.items():
        here = f"{prefix}{key}"
        if isinstance(value, dict) and value and depth > 1:
            out.extend(_paths(value, depth - 1, here + "."))
        else:
            out.append(here)
    return out
