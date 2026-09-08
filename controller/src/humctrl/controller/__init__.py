from humctrl.utils import Unset, UnsetType, require

from .errors import (
    ControlLawNotRegisteredError,
    ControllerSuspendedError,
    LastReadingNotAvailableError,
)
from .laws import PI, PID, OpenLoop, OpenLoopTuning, P
from .setpoint import LinearRamp, SetPointGenerator
from .types import (
    ControlLaw,
    ControlLawConfig,
    ControlLawLike,
    ControlLaws,
    ControlLawState,
    ControlLawView,
    ControllerState,
    ControllerView,
    Transfer,
    Tuning,
    ValueSource,
)

__all__ = [
    "PI",
    "PID",
    "ControlLaw",
    "ControlLawConfig",
    "ControlLawLike",
    "ControlLawNotRegisteredError",
    "ControlLawState",
    "ControlLawView",
    "ControlLaws",
    "Controller",
    "ControllerState",
    "ControllerSuspendedError",
    "ControllerView",
    "LastReadingNotAvailableError",
    "LinearRamp",
    "OpenLoop",
    "OpenLoopTuning",
    "P",
    "SetPointGenerator",
    "Transfer",
    "Tuning",
    "ValueSource",
    "get_law_config",
]


def get_law_config(tag: str, *args, **kwargs) -> ControlLawConfig:
    if law := ControlLaws.get(tag):
        return law.config(*args, **kwargs)
    raise ControlLawNotRegisteredError(tag)


class Controller:
    generator: SetPointGenerator | None = None
    _setpoint: float
    law: ControlLaw
    correction: float = 0.0
    last_value: float | None = None

    def __init__(
        self,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning,
        setpoint: float,
        time: float,
    ) -> None:
        self.correction = 0.0
        self._setpoint = setpoint
        self.set_law(law, time)

    def set_law(
        self, law: ControlLaw | ControlLawConfig | ControlLawView | Tuning, time: float
    ) -> None:
        self._set_law(law)
        self.law.start(time)

    def _set_law(self, law: ControlLaw | ControlLawConfig | ControlLawView | Tuning) -> None:
        self.law = law if isinstance(law, ControlLaw) else law.build()

    def reset(self, time: float, value: float | UnsetType | None = Unset) -> None:
        self.correction = 0.0
        if value is not Unset:
            self.last_value = value
        self._update_setpoint(time)
        self.law.start(time)

    @property
    def setpoint(self) -> float:
        return self._setpoint

    @property
    def demand(self) -> float:
        return self.setpoint + self.correction

    @property
    def state(self) -> ControllerState:
        return ControllerState(
            generator=self.generator is not None,
            setpoint=self.setpoint,
            correction=self.correction,
            law=self.law.state,
            last_value=self.last_value,
        )

    @property
    def spec(self) -> ControlLawConfig:
        return self.law.config

    @property
    def view(self) -> ControllerView:
        return ControllerView(
            generator=self.generator is not None,
            setpoint=self.setpoint,
            correction=self.correction,
            last_value=self.last_value,
            law=self.law.view,
        )

    def demand_at(self, time: float | None = None) -> float:
        return self.setpoint_at(time) + self.correction

    def setpoint_at(self, time: float | None) -> float:
        if self.generator is not None and time is not None:
            return self.generator.generate(time)
        return self._setpoint

    @property
    def tag(self) -> str:
        return self.law.tag

    def _update_setpoint(self, time: float) -> None:
        if self.generator is not None:
            self._setpoint = self.generator.generate(time)

    def _new_value(self, time: float, value: float) -> None:
        self.last_value = value
        self._update_setpoint(time)

    def set_setpoint(
        self,
        setpoint: float | ValueSource,
        time: float | None = None,
    ) -> None:
        self._setpoint = self.resolve_value(setpoint, time)
        self.generator = None

    def set_reference(
        self, at: float | ValueSource, time: float, generator: SetPointGenerator | None
    ) -> None:
        self._setpoint = self.resolve_value(at, time)
        self.generator = generator
        if self.generator is not None:
            self.generator.start(time, self._setpoint)
            self._setpoint = self.generator.generate(time)

    def set_generator(
        self,
        generator: SetPointGenerator,
        time: float,
        at: ValueSource | float = ValueSource.SETPOINT,
    ) -> None:
        self.set_reference(at=at, time=time, generator=generator)

    def resolve_value(self, start_from: ValueSource | float, time: float | None = None) -> float:
        if start_from is ValueSource.PROCESS:
            return require(self.last_value, LastReadingNotAvailableError)
        if start_from is ValueSource.SETPOINT:
            return self.setpoint_at(time)
        if start_from is ValueSource.DEMAND:
            return self.demand_at(time)
        return float(start_from)

    def step(self, time: float, value: float) -> None:
        self._new_value(time, value)
        self.correction = self.law.step(time, value, self.setpoint, self.correction)

    def resume(self, time: float, value: float, expected: float | None = None) -> None:
        """Resume the controller from a given state.

        Args:
            time: The time to resume from.
            value: The value to resume from.
            expected: The expected value to resume from.
        """
        self._new_value(time, value)
        if expected is not None:
            self.correction = expected - self.setpoint
        self.correction = self.law.resume(time, value, self.setpoint, self.correction)

    def _seed(self, time: float, mode: Transfer, expected: float | None) -> float:
        """Set ``correction`` so the first step reproduces the chosen output.

        Degrades rather than fails: ``TRACK`` needs something to be delivered, and
        anything but ``COLD`` needs a measurement to compute the law's proportional
        term. Where either is missing the seed falls back, and the returned bump is
        what tells the caller it did.
        """
        if mode is Transfer.NONE:
            return 0.0
        reference = self.demand if expected is None else expected
        if self.last_value is None or mode is Transfer.RESET:
            self.reset(time)
        else:
            self.resume(time, self.last_value, None if mode is Transfer.CARRY else expected)
        return self.demand - reference

    def transfer(
        self,
        time: float,
        law: ControlLawLike | None = None,
        mode: Transfer = Transfer.TRACK,
        expected: float | None = None,
    ) -> float:
        """Re-seed the correction. Returns the bump this will put through."""
        if law is not None:
            self._set_law(law)
        return self._seed(time, mode, expected)
