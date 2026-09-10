from humctrl.core.utils import require

from .errors import ControllerNotStartedError
from .setpoint import SetPointGenerator
from .types import (
    ControlLaw,
    ControlLawConfig,
    ControlLawLike,
    ControlLawState,
    ControlLawView,
    ControllerState,
    ControllerView,
    Transfer,
    Tuning,
)


class Controller:
    law: ControlLaw
    correction: float = 0.0
    _setpoint: float | None = None
    generator: SetPointGenerator | None = None
    last_value: float | None = None
    started: bool = False

    def __init__(
        self,
        law: ControlLaw | ControlLawConfig | ControlLawView | Tuning,
    ) -> None:
        self._set_law(law)

    def start(self, time_ns: int, at: float, generator: SetPointGenerator | None) -> None:
        self.set_reference(at=at, time_ns=time_ns, generator=generator)

    def _ensure_started_setpoint(self, time_ns: int) -> float:
        setpoint = self.require_setpoint
        self._ensure_started(time_ns)
        return setpoint

    def _ensure_started(self, time_ns: int) -> None:
        if not self.started:
            self.law.start(time_ns)
            self.started = True

    def require_started(self) -> None:
        if not self.started:
            raise ControllerNotStartedError()

    def _set_law(self, law: ControlLaw | ControlLawConfig | ControlLawView | Tuning) -> None:
        self.law = law if isinstance(law, ControlLaw) else law.build()

    def reset(self, time_ns: int) -> None:
        self.correction = 0.0
        self._update_setpoint(time_ns)
        self.law.start(time_ns)

    @property
    def demand(self) -> float:
        return self.require_setpoint + self.correction

    @property
    def state(self) -> ControllerState:
        return ControllerState(
            generator=self.generator is not None,
            setpoint=self.setpoint,
            correction=self.correction,
            law=self.law_state,
            last_value=self.last_value,
        )

    @property
    def setpoint(self) -> float | None:
        return self._setpoint

    @property
    def require_setpoint(self) -> float:
        return require(self._setpoint, ControllerNotStartedError)

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
            law=self.law_view,
        )

    @property
    def law_state(self) -> ControlLawState | Exception:
        try:
            return self.law.state
        except Exception as e:
            return e

    @property
    def law_view(self) -> ControlLawView | Exception:
        try:
            return self.law.view
        except Exception as e:
            return e

    def demand_at(self, time_ns: int) -> float:
        return self.setpoint_at(time_ns) + self.correction

    def setpoint_at(self, time_ns: int) -> float:
        if self.generator is not None and time_ns is not None:
            return self.generator.generate(time_ns)
        return self.require_setpoint

    @property
    def tag(self) -> str:
        return self.law.tag

    def _update_setpoint(self, time_ns: int) -> None:
        if self.generator is not None:
            self._setpoint = self.generator.generate(time_ns)

    def _new_value(self, time_ns: int, value: float) -> None:
        self.last_value = value
        self._update_setpoint(time_ns)

    def set_setpoint(
        self,
        setpoint: float,
    ) -> None:
        self._setpoint = setpoint
        self.generator = None

    def set_reference(self, at: float, time_ns: int, generator: SetPointGenerator | None) -> None:
        self._setpoint = at
        self._ensure_started(time_ns)
        self.generator = generator
        if self.generator is not None:
            self.generator.start(time_ns, self._setpoint)
            self._setpoint = self.generator.generate(time_ns)

    def set_generator(
        self,
        generator: SetPointGenerator,
        time_ns: int,
        at: float,
    ) -> None:
        self.set_reference(at=at, time_ns=time_ns, generator=generator)

    def step(self, time_ns: int, value: float) -> None:
        setpoint = self._ensure_started_setpoint(time_ns)
        self._new_value(time_ns, value)
        self.correction = self.law.step(time_ns, value, setpoint, self.correction)

    def resume(self, time_ns: int, value: float, expected: float | None = None) -> None:
        """Resume the controller from a given state.

        Args:
            time_ns: The time_ns to resume from.
            value: The value to resume from.
            expected: The expected value to resume from.
        """
        if not self.started or self._setpoint is None:
            self._setpoint = self.last_value if expected is None else expected
            self._setpoint = self._ensure_started_setpoint(time_ns)

        self._resume(time_ns, value, self._setpoint, expected)

    def _resume(
        self, time_ns: int, value: float, setpoint: float, expected: float | None = None
    ) -> None:
        self._new_value(time_ns, value)
        if expected is not None:
            self.correction = expected - setpoint
        self.correction = self.law.resume(time_ns, value, setpoint, self.correction)

    def _seed(self, time_ns: int, mode: Transfer, expected: float | None) -> float:
        """Set ``correction`` so the first step reproduces the chosen output.

        Degrades rather than fails: ``TRACK`` needs something to be delivered, and
        anything but ``COLD`` needs a measurement to compute the law's proportional
        term. Where either is missing the seed falls back, and the returned bump is
        what tells the caller it did.
        """
        if mode is Transfer.NONE:
            return 0.0

        if self._setpoint is None or not self.started:
            self._setpoint = expected if self._setpoint is None else self._setpoint
            self._setpoint = self._ensure_started_setpoint(time_ns)

        reference = self.demand if expected is None else expected
        if self.last_value is None or mode is Transfer.RESET:
            self.reset(time_ns)
        else:
            self._resume(
                time_ns,
                self.last_value,
                self._setpoint,
                None if mode is Transfer.CARRY else expected,
            )
        return self.demand - reference

    def transfer(
        self,
        time_ns: int,
        law: ControlLawLike | None = None,
        mode: Transfer = Transfer.TRACK,
        expected: float | None = None,
    ) -> float:
        """Re-seed the correction. Returns the bump this will put through."""
        if law is not None:
            self._set_law(law)
        return self._seed(time_ns, mode, expected)
