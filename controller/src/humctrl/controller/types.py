from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from typing import Any, ClassVar, Protocol

from humctrl.config import Config
from humctrl.pumps.types import BlendFlow, DryWet
from humctrl.typing import Percent


class ControlLaw(Protocol):
    #: Tag shared with the matching :class:`ControlLawConfig` and the ``type``
    #: key in config files. Set by :func:`control_law`; a law that forgets the
    #: decorator reports "". Shadows the builtin inside a class body only.
    type: ClassVar[str] = ""

    def start(self, time: float, reading: float) -> None:
        return None

    def resume(self, time: float, reading: float, set_point: float, correction: float) -> float:
        """Re-enter control so the first step reproduces ``correction``.

        Used to hand back from manual pump control without stepping the output.
        Returns the correction actually seeded, which is not ``correction`` when
        the law has no integral to carry an offset: a proportional law cannot be
        bumpless because it has no memory. Compare the two to size the bump the
        hand-back will put through the pumps.

        The default is a cold start carrying no offset. A law that can compute
        the correction it will actually produce should override this.
        """
        self.start(time, reading)
        return correction

    def step(
        self,
        time: float,
        reading: float,
        set_point: float,
        last_applied: float | None = None,
    ) -> float: ...

    @property
    def state(self) -> Any | None:
        return None

    @cached_property
    def config(self) -> ControlLawConfig:
        raise NotImplementedError


class ControlLawConfig(Config[ControlLaw]):
    type: str

    def build(self) -> ControlLaw:
        raise NotImplementedError


class Rail(Enum):
    WET = "wet"
    DRY = "dry"

    def __float__(self) -> float:
        match self:
            case Rail.WET:
                return 1.0
            case Rail.DRY:
                return 0.0


@dataclass(slots=True, frozen=True)
class ControlLawView(ControlLawConfig):
    spec: ControlLawConfig
    state: Any | None


@dataclass(slots=True, frozen=True)
class ControllerState:
    set_point: Percent
    flow: BlendFlow
    flow_humidities: DryWet[Percent]
    suspended: bool


@dataclass(slots=True, frozen=True)
class ClosedControllerState(ControllerState):
    set_point: Percent
    demand: Percent
    flow: BlendFlow
    flow_humidities: DryWet[Percent]
    law: Any | None
    suspended: bool


@dataclass(slots=True, frozen=True)
class ControllerView(ControllerState):
    law: str | ControlLawView

    @classmethod
    def from_spec_state(
        cls, spec: ControlLawConfig | str, state: ControllerState
    ) -> ControllerView:
        return cls(
            set_point=state.set_point,
            flow=state.flow,
            flow_humidities=state.flow_humidities,
            suspended=state.suspended,
            law=ControlLawView(spec=spec, state=None)
            if isinstance(spec, ControlLawConfig)
            else spec,
        )


@dataclass(slots=True, frozen=True)
class ClosedControllerView(ClosedControllerState, ControllerView):
    law: str | ControlLawView

    @classmethod
    def from_spec_state(
        cls, spec: ControlLawConfig, state: ClosedControllerState
    ) -> ClosedControllerView:
        return cls(
            set_point=state.set_point,
            demand=state.demand,
            flow=state.flow,
            flow_humidities=state.flow_humidities,
            law=ControlLawView(spec=spec, state=state.law),
            suspended=state.suspended,
        )
