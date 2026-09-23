"""Signal faults stage 2, the rig-lock rule: nothing waits or does I/O while holding `rig.lock`."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
from flyball_sim import SteppedClock

from flyball.foundation.device import (
    Access,
    Device,
    Node,
    Readable,
    Role,
    Sample,
    SignalSpec,
    command,
)
from flyball.foundation.errors import ConflictError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import One
from flyball.rig import Rig
from flyball.sequencing import Programmer
from flyball.sequencing.devices import RunCommand

COUNT = Quantity("count", One)


class Doser(Device):
    """`dose(seconds)` waits the whole dose, as `dosing_pump.dispense` does; `stop` ends it."""

    TREE = (
        SignalSpec(name="dosed_s", quantity=COUNT, access=Access.RP, role=Role.READOUT, initial=0),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.started = threading.Event()
        self.stopped_early: bool | None = None

    @command(long=True)
    def dose(self, seconds: float) -> None:
        """Run for `seconds` of the rig's time, or until `stop`."""
        self.started.set()
        self.stopped_early = self.wait(seconds)
        self.signals["dosed_s"].push(seconds)

    @command
    def stop(self) -> None:
        """End a dose in progress."""
        self.cancel()


class Ticker(Readable):
    """Counts its reads: a device polled on its period."""

    TREE = (SignalSpec(name="n", quantity=COUNT, access=Access.RP, role=Role.READOUT),)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.reads = 0

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.reads += 1
        yield Sample(self.root, time_ns, {self.signals["n"]: float(self.reads)})


def _in_thread(fn, *args) -> tuple[threading.Thread, list[object]]:  # noqa: ANN001
    out: list[object] = []

    def run() -> None:
        try:
            out.append(fn(*args))
        except Exception as error:  # the test reads it
            out.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


# region 1. Long commands off the lock


class TestLongCommands:
    def test_a_stop_during_a_long_command_takes_effect_at_once(self, fresh):
        rig = Rig()  # the wall clock: a 30 s dose would really wait 30 s
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        thread, out = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        began = time.monotonic()
        rig.run_command(doser, "stop")
        thread.join(2.0)
        assert not thread.is_alive(), "the dose ended when stopped, not after 30 s"
        assert time.monotonic() - began < 1.0
        assert out == [None] and doser.stopped_early is True
        assert rig.router.value(doser.signals["dosed_s"]) == 30.0, "pushed after, under the lock"
        rig.close()

    def test_polling_and_deliveries_carry_on_during_a_long_command(self, fresh):
        rig = Rig()
        doser, ticker = Doser(fresh("doser")), Ticker(fresh("ticker"))
        ticker.poll_s = 0.01
        rig.add_device(doser)
        rig.add_device(ticker)
        rig.start_polling(ticker)
        thread, _ = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        before = ticker.reads
        deadline = time.monotonic() + 2.0
        while ticker.reads < before + 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ticker.reads >= before + 5, "the poller delivered while the dose waited"
        n = ticker.signals["n"]
        assert rig.latest[n].value >= before + 5, "and each read was delivered"
        assert rig.lock.acquire(timeout=0.5), "the rig lock is free during the dose"
        rig.lock.release()
        rig.run_command(doser, "stop")
        thread.join(2.0)
        assert not thread.is_alive()
        rig.close()

    def test_a_second_long_command_on_the_device_is_refused(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        thread, _ = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        with pytest.raises(ConflictError, match="is running 'dose'"):
            rig.run_command(doser, "dose", {"seconds": 1.0})
        rig.run_command(doser, "stop")
        thread.join(2.0)
        doser.started.clear()
        rig.run_command(doser, "dose", {"seconds": 0.01})  # free again, and not pre-cancelled
        assert doser.stopped_early is False
        rig.close()

    def test_a_long_command_is_refused_under_the_rig_lock(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        with rig.lock, pytest.raises(ConflictError, match="rig lock is held"):
            rig.run_command(doser, "dose", {"seconds": 1.0})
        assert not doser.started.is_set()

    def test_a_long_command_waits_on_the_rigs_clock(self, fresh):
        rig = Rig()
        rig.clock = SteppedClock(0)
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        began = time.monotonic()
        rig.run_command(doser, "dose", {"seconds": 600.0})
        assert time.monotonic() - began < 1.0, "a stepped clock steps past the wait"
        assert rig.clock.now_ns() == 600 * 10**9
        assert doser.stopped_early is False

    def test_a_program_step_runs_a_long_command_off_the_rig_lock(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        programmer = Programmer(rig)
        thread, _ = _in_thread(
            programmer.run,
            RunCommand(device_command="dose", device=doser.name, args={"seconds": 30}),
        )
        assert doser.started.wait(2.0)
        assert rig.lock.acquire(timeout=0.5), "the step does not hold the rig lock across the dose"
        rig.lock.release()
        rig.run_command(doser, "stop")
        thread.join(2.0)
        assert not thread.is_alive()

    def test_removing_the_device_ends_its_long_command(self, fresh):
        rig = Rig()
        doser = Doser(fresh("doser"))
        rig.add_device(doser)
        thread, out = _in_thread(rig.run_command, doser, "dose", {"seconds": 30.0})
        assert doser.started.wait(2.0)
        rig.remove_device(doser.name)
        thread.join(2.0)
        assert not thread.is_alive() and out == [None]
        assert doser.signals["dosed_s"] not in rig.latest, "what it pushed after went nowhere"


# endregion
