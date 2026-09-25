"""The rig's lock is its own (D-098): nothing outside `flyball.rig` takes it.

A source scan, over the engine and every package beside it in this repository, for an
attribute `_lock` read off something named as the rig (`rig._lock`, `self.rig._lock`,
`self._rig._lock`); comments and strings are not code, so they may still name it. And the
programmer, the one client that used to take it around its steps, runs them holding no lock.
"""

from __future__ import annotations

import io
import threading
import tokenize
from pathlib import Path

from flyball.rig import Rig
from flyball.sequencing import Program, Programmer, Wait
from flyball.sequencing.activities import Prompt

ENGINE = Path(__file__).resolve().parents[1] / "src" / "flyball"
REPO = ENGINE.parents[2]
RIG = ENGINE / "rig"


def _sources() -> list[Path]:
    """Every Python source file of the engine, and of the packages beside it that use it."""
    roots = [ENGINE]
    for pattern in ("sim/src", "extensions/*/src", "examples/*/src", "examples/*/*.py"):
        roots.extend(REPO.glob(pattern))
    found: list[Path] = []
    for root in roots:
        found.extend([root] if root.is_file() else root.rglob("*.py"))
    return sorted(p for p in found if ".venv" not in p.parts and not p.is_relative_to(RIG))


def _rig_like(token: tokenize.TokenInfo) -> bool:
    return token.type == tokenize.NAME and (
        token.string in ("rig", "_rig") or token.string.endswith("_rig")
    )


def _takes_the_rig_lock(source: str) -> list[int]:
    """The lines on which code reads `_lock` off a name for the rig (`._lock`, or by `getattr`)."""
    tokens = [
        t
        for t in tokenize.generate_tokens(io.StringIO(source).readline)
        if t.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE)
    ]
    lines = []
    for i, token in enumerate(tokens):
        after = [t.string for t in tokens[i + 1 : i + 5]]
        if _rig_like(token) and after[:2] == [".", "_lock"]:
            lines.append(token.start[0])
        if (
            token.string == "getattr"
            and after[:1] == ["("]
            and len(tokens) > i + 4
            and _rig_like(tokens[i + 2])
            and after[2:4] in ([",", '"_lock"'], [",", "'_lock'"])
        ):
            lines.append(token.start[0])
    return lines


def test_nothing_outside_the_rig_takes_its_lock():
    sources = _sources()
    assert len(sources) > 100, "the scan found the engine's sources"
    offenders = [
        f"{path.relative_to(REPO)}:{line}"
        for path in sources
        for line in _takes_the_rig_lock(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "rig._lock taken outside flyball.rig (use a rig operation):\n" + "\n".join(offenders)
    )


def test_the_scan_sees_what_it_is_for():
    assert _takes_the_rig_lock("with rig._lock:\n    pass\n") == [1]
    assert _takes_the_rig_lock("x = self.rig._lock\n") == [1]
    assert _takes_the_rig_lock("self._rig._lock.acquire()\n") == [1]
    assert _takes_the_rig_lock("# rig._lock in a comment\ns = 'rig._lock'\n") == []
    assert _takes_the_rig_lock('getattr(rig, "_lock")\n') == [1]
    assert _takes_the_rig_lock('getattr(rig, "name")\n') == []
    assert _takes_the_rig_lock("self._lock = RLock()\nprogrammer._lock\nrig._locked\n") == []


def test_a_program_applies_its_steps_without_the_rig_lock():
    """A step that changes nothing waits for nothing: another thread holding the lock is no bar.

    Before, every step but a device command ran under `rig._lock`, and the programmer took it
    around the step; so a `wait` or a `prompt` queued behind a delivery (and the programmer
    was one side of a lock-order pair with the rig).
    """
    rig = Rig()
    programmer = Programmer(rig)
    holding, release = threading.Event(), threading.Event()

    def hold() -> None:
        with rig._lock:
            holding.set()
            release.wait(5.0)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert holding.wait(2.0)
    try:
        started = threading.Event()

        def start() -> None:
            programmer.start(Program([Prompt(message="load it"), Wait(duration=1)]))  # type: ignore[arg-type]
            started.set()

        threading.Thread(target=start, daemon=True).start()
        assert started.wait(1.0), "the first step applied while another thread held the lock"
        assert programmer.state.type == "prompt"
    finally:
        release.set()
        thread.join(2.0)
        programmer.cancel()
