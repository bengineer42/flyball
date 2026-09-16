"""What the rig is waiting on, by name, so a person or a client can answer.

Anything that blocks a program is a [Signal][flyball.core.signal.Signal].
Registering it here gives it a name and a message, so the server can list,
fire or interrupt it. Outcomes are pushed through `latest` as they settle.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from flyball.core.clock import Clock
from flyball.core.errors import ConflictError, NotFoundError
from flyball.core.signal import Outcome, Signal
from flyball.core.topic import Latest


@dataclass(frozen=True, slots=True)
class SignalState:
    name: str
    message: str | None
    outcome: Outcome
    since_ns: int
    timeout_s: float | None
    prompt: bool = False
    """Waiting on a person: only these deserve a button. The rest settle on their own."""


@dataclass(frozen=True, slots=True)
class _Entry:
    signal: Signal
    state: SignalState


class Signals:
    """The rig's named signals. One name at a time; removed when its wait is over."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = Lock()
        self._entries: dict[str, _Entry] = {}
        self.latest: Latest[str, SignalState] = Latest()
        """Every registration and settlement, by name, for the telemetry flush."""

    def register(
        self,
        name: str,
        signal: Signal,
        message: str | None = None,
        timeout_s: float | None = None,
        prompt: bool = False,
    ) -> SignalState:
        """Name a signal while something waits on it.

        Args:
            name: What it is fired by: `POST /api/signals/{name}/fire`.
            signal: What is waited on.
            message: What the wait is for, for a person.
            timeout_s: When the wait gives up, if it does.
            prompt: Nothing but a person (or an external trigger) fires it,
                so a UI should ask. A hold or an arrival settles on its own.

        Raises:
            ConflictError: `name` is already waiting on something else.
        """
        state = SignalState(name, message, signal.outcome, self._clock.now_ns(), timeout_s, prompt)
        with self._lock:
            if (existing := self._entries.get(name)) is not None and existing.signal is not signal:
                raise ConflictError(f"Signal {name!r} is already pending")
            self._entries[name] = _Entry(signal, state)
        signal.on_settle = lambda s: self._settled(name, s)
        self.latest.set(name, state)
        return state

    def remove(self, name: str) -> None:
        """Forget a signal once its wait is over. The last state stays in `latest`."""
        with self._lock:
            entry = self._entries.pop(name, None)
        if entry is not None:
            entry.signal.on_settle = None

    def _settled(self, name: str, signal: Signal) -> None:
        with self._lock:
            entry = self._entries.get(name)
            if entry is None or entry.signal is not signal:
                return
            state = SignalState(
                name,
                entry.state.message,
                signal.outcome,
                entry.state.since_ns,
                entry.state.timeout_s,
                entry.state.prompt,
            )
            self._entries[name] = _Entry(signal, state)
        self.latest.set(name, state)

    def _signal(self, name: str) -> Signal:
        with self._lock:
            if (entry := self._entries.get(name)) is None:
                raise NotFoundError(f"No signal {name!r} is pending")
            return entry.signal

    def fire(self, name: str) -> bool:
        """Settle `name` as met -- the operator answered, or wants the wait skipped."""
        return self._signal(name).fire()

    def interrupt(self, name: str) -> bool:
        return self._signal(name).interrupt()

    def state(self, name: str) -> SignalState:
        with self._lock:
            if (entry := self._entries.get(name)) is None:
                raise NotFoundError(f"No signal {name!r} is pending")
            return entry.state

    def states(self) -> dict[str, SignalState]:
        """Everything registered, settled or not, until its wait removes it."""
        with self._lock:
            return {name: entry.state for name, entry in self._entries.items()}
