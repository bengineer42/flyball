"""`Tuning`: a saved, named `ControlLawConfig`/`ControlLawView`, independent of any one rig.

Moved off `Rig` (`brain/tasks/registry-redesign.md`, 21 Sep -- "Rig should NOT
own it"): once a rig is "just the runtime instance," a saved/named config
doesn't belong on it either, any more than a saved program does
(`server/routes/library.py`'s program library is the same shape: saved,
named, versioned, read by name). Standalone for now -- wiring `Rig`/the
server to read tunings from here rather than `rig.tunings` is a future pass,
not this one.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SerializeAsAny

from flyball.model.law import ControlLaw, ControlLawBuilder, ControlLawConfig, ControlLawView


@dataclass(slots=True, frozen=True)
class Tuning:
    tag: str
    # Serialised by its runtime type: declared as the base, a response would
    # carry only `tag` and drop every gain the law actually has.
    config: SerializeAsAny[ControlLawBuilder]

    def build(self) -> ControlLaw:
        return self.config.build()

    @property
    def tuple(self) -> tuple[str, SerializeAsAny[ControlLawBuilder]]:
        return (self.tag, self.config)


class Tunings:
    def __init__(self, tunings: list[Tuning] | None = None) -> None:
        self._tunings: dict[str, ControlLawBuilder] = {}
        if tunings is not None:
            for tuning in tunings:
                if tuning.tag in self._tunings:
                    raise ValueError(f"duplicate tuning tag {tuning.tag!r}")
                self._tunings[tuning.tag] = tuning.config

    def add(self, tuning: Tuning) -> None:
        self._tunings[tuning.tag] = tuning.config

    def get(self, tag: str) -> SerializeAsAny[ControlLawBuilder] | None:
        return self._tunings.get(tag)

    def all(self) -> dict[str, ControlLawConfig | ControlLawView]:
        return dict(self._tunings)

    def list(self) -> list[Tuning]:
        return [Tuning(tag=tag, config=config) for tag, config in self._tunings.items()]


def _open_loop_tuning() -> Tuning:
    from flyball.control.laws import OpenLoop

    return Tuning("open_loop", OpenLoop.config())


OpenLoopTuning = _open_loop_tuning()
"""A ready-made `Tuning` for `OpenLoop`. Moved here from `control/laws.py`, which can't
build a `Tuning` itself without `control` depending upward on `library`; nothing else in
the repo references it today (checked), so the relocation is otherwise a plain move."""
