"""The rig: attaching devices, a delivery, telemetry cells, reader runs."""

from __future__ import annotations

import pytest

from flyball.control import PI
from flyball.core.device import Level
from flyball.core.errors import ConflictError
from flyball.core.reading import Reader, Source
from flyball.sim import FunctionReader, RecordingActuator
from helpers import sample


def test_attach_loop_registers_the_actuator_by_name(rig, probe, temperature, heater):
    rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
    assert rig.actuators[heater.name] is heater
    assert probe in rig.sources


def test_add_actuator_refuses_clashes_and_reserved_names(rig, fresh):
    a = RecordingActuator(fresh("a"))
    rig.add_actuator(a)
    rig.add_actuator(a)  # the same object again is fine
    with pytest.raises(ConflictError, match="already attached"):
        rig.add_actuator(RecordingActuator(a.name))
    with pytest.raises(ConflictError, match="reserved"):
        rig.add_actuator(RecordingActuator("schema"))


def test_a_delivery_ticks_the_loop_and_applies_the_actuator(rig, probe, temperature, heater, clock):
    rig.attach_loop(probe[temperature], heater, law=PI(kp=2.0))
    loop = rig.loops[heater.name]
    loop.regulate(50.0)
    rig.on_read([sample(probe, temperature, 40.0, clock.now_ns())])
    assert loop.reading is not None and loop.reading.value == 40.0
    assert heater.demands[-1] == pytest.approx(50.0 + 2.0 * 10.0)
    assert heater.applied == 1


def test_cells_are_filled_only_while_watched(rig, probe, temperature, heater, clock):
    rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
    rig.loops[heater.name].regulate(5.0)
    rig.on_read([sample(probe, temperature, 1.0, clock.now_ns())])
    assert rig.loop_states.changed_since(0)[1] == {}
    assert rig.actuator_states.changed_since(0)[1] == {}
    with rig.loop_states.watch(), rig.actuator_states.watch():
        rig.on_read([sample(probe, temperature, 2.0, clock.now_ns(), seq=2)])
    states = rig.loop_states.changed_since(0)[1]
    assert states[heater.name].reading.value == 2.0
    assert rig.actuator_states.changed_since(0)[1][heater.name].demand == heater.demands[-1]


def test_apply_publishes_actuator_state_after_a_command(rig, duty_heater):
    rig.add_actuator(duty_heater)
    with rig.actuator_states.watch():
        duty_heater.set_duty(0.7)
        rig.apply(duty_heater)
    assert rig.actuator_states.changed_since(0)[1][duty_heater.name].duty == 0.7


def test_reader_run_records_reads_and_goes_offline_on_error(rig, probe, temperature, clock, fresh):
    class Flaky(Reader):
        fail = False

        def __init__(self):
            super().__init__(fresh("flaky"), (probe,))

        def read(self, time_ns):
            if self.fail:
                raise OSError("I2C timeout")
            return [sample(probe, temperature, 20.0, time_ns)]

    reader = Flaky()
    rig.readers.add(reader)
    rig.readers._read(reader)
    run = rig.readers.run(reader.name)
    assert run.last_read_ns == clock.now_ns() and run.conditions == ()
    reader.fail = True
    rig.readers._read(reader)  # the poll swallows it: the run says so, and an event is raised
    run = rig.readers.run(reader.name)
    assert run.running is False
    assert run.conditions[0].kind == "offline" and run.conditions[0].level is Level.ERROR
    assert "I2C timeout" in run.conditions[0].message
    assert rig.recent[-1].kind == "offline" and rig.recent[-1].subject == reader.name
    reader.fail = False
    assert rig.readers.restart(reader.name).conditions == ()
    assert rig.recent[-1].kind == "restarted"


def test_a_failure_downstream_of_a_read_is_not_the_reader_s(rig, probe, temperature, fresh):
    from flyball.core.reading import Reader
    from flyball.core.sink import Observer

    class Broken(Observer):
        observes = frozenset({probe})
        touches = frozenset()

        def observe(self, sample):
            raise ValueError("a bug in an observer")

    class Fine(Reader):
        def read(self, time_ns):
            return [sample(probe, temperature, 1.0, time_ns)]

    rig.attach_observer(Broken())
    reader = Fine(fresh("fine"), (probe,))
    rig.readers.add(reader)
    rig.readers._read(reader)
    run = rig.readers.run(reader.name)
    assert run.conditions == () and run.last_read_ns is not None, "the reader read fine"
    event = rig.recent[-1]
    assert event.kind == "delivery_failed" and event.scope == "rig"
    assert "a bug in an observer" in event.message


def test_function_reader_stamps_every_source_with_one_instant(fresh, temperature):
    a, b = Source(fresh("a"), (temperature,)), Source(fresh("b"), (temperature,))
    reader = FunctionReader(
        fresh("sim"), {a: lambda t: {temperature: 1.0}, b: lambda t: {temperature: 2.0}}
    )
    samples = list(reader.read(123))
    assert [s.time_ns for s in samples] == [123, 123]
    assert {s.source: s.values[temperature] for s in samples} == {a: 1.0, b: 2.0}
    assert reader.channels == {a[temperature], b[temperature]}


def test_a_pushed_sample_reaches_the_rig_at_once(rig, probe, temperature, fresh):
    from flyball.core.reading import Reader

    reader = Reader(fresh("mqtt"), (probe,))
    rig.start_reader(reader)  # no period: push only
    reader.push(probe, {temperature: 3.0}, time_ns=50)
    assert rig._readings[probe[temperature]].value == 3.0, "delivered without waiting for a poll"
    assert rig.readers.run(reader.name).last_read_ns == 50


def test_samples_pushed_before_attaching_are_delivered_on_attach(rig, probe, temperature, fresh):
    from flyball.core.reading import Reader

    reader = Reader(fresh("early"), (probe,))
    reader.push(probe, {temperature: 1.0}, time_ns=10)
    reader.push(probe, {temperature: 2.0}, time_ns=20)
    assert probe[temperature] not in rig._readings
    rig.start_reader(reader)
    assert rig._readings[probe[temperature]].value == 2.0
    assert [s.seq for s in [rig._samples[probe]]] == [2], "seq is the source's, in order"


def test_a_polled_reader_still_polls_and_records_its_run(rig, probe, temperature, fresh, clock):
    from flyball.core.reading import Reader, Sample

    class Polled(Reader):
        def read(self, time_ns):
            return [Sample(probe, probe.next_seq(), time_ns, {temperature: 7.0})]

    reader = Polled(fresh("polled"), (probe,))
    rig.readers.add(reader)
    clock.advance(1.0)
    rig.readers._read(reader)
    assert rig._readings[probe[temperature]].value == 7.0
    assert rig.readers.run(reader.name).last_read_ns == clock.now_ns()


def test_a_mixed_reader_delivers_both_paths(rig, probe, temperature, fresh, clock):
    from flyball.core.reading import Reader, Sample

    class Mixed(Reader):
        def read(self, time_ns):
            return [Sample(probe, probe.next_seq(), time_ns, {temperature: 1.0})]

    reader = Mixed(fresh("mixed"), (probe,))
    rig.start_reader(reader)
    rig.read(reader)
    assert rig._readings[probe[temperature]].value == 1.0
    reader.push(probe, {temperature: 2.0}, time_ns=clock.now_ns() + 1)
    assert rig._readings[probe[temperature]].value == 2.0


def test_attach_loop_picks_the_feedforward_by_unit(rig, probe, temperature, fresh):
    from flyball.control import NoFeedforward, Setpoint
    from flyball.core.errors import ConflictError
    from flyball.core.units.si import Kelvin, Volt

    class VoltsIn(RecordingActuator):
        demand_unit = Volt

    class KelvinIn(RecordingActuator):
        demand_unit = Kelvin

    same, psu = RecordingActuator(fresh("same")), VoltsIn(fresh("psu"))
    rig.attach_loop(probe[temperature], same, law=PI(kp=1.0))
    assert isinstance(rig.loops.resolve(same.name).feedforward, Setpoint)
    rig.loops.remove(same.name)
    rig.attach_loop(probe[temperature], psu, law=PI(kp=1.0))
    assert isinstance(rig.loops.resolve(psu.name).feedforward, NoFeedforward)
    rig.loops.remove(psu.name)
    # Passing the setpoint straight to an actuator that takes another unit is nonsense,
    # even the same dimension in a different unit.
    with pytest.raises(ConflictError, match="takes demands in K"):
        rig.attach_loop(
            probe[temperature], KelvinIn(fresh("k")), law=PI(kp=1.0), feedforward="setpoint"
        )
    rig.attach_loop(probe[temperature], KelvinIn(fresh("k2")), law=PI(kp=1.0), feedforward="none")


def test_a_slow_read_raises_a_warning_condition(rig, probe, temperature, fresh):
    from flyball.core.device import Level
    from flyball.core.reading import Reader

    class Slow(Reader):
        def read(self, time_ns):
            rig.clock.sleep(0.02)  # the rig's time is what "slow" is judged in
            return [sample(probe, temperature, 1.0, time_ns)]

    reader = Slow(fresh("slow"), (probe,))
    rig.readers.start_periodic(reader, period=0.005)
    rig.readers.stop_all()
    rig.readers._read(reader)
    (condition,) = rig.readers.run(reader.name).conditions
    assert condition.kind == "slow" and condition.level is Level.WARNING


class TestWriters:
    """A blocking actuator's writes happen on its own thread; a failing bus is a condition."""

    def test_the_loop_never_waits_for_a_blocking_actuator(self, rig, probe, temperature, fresh):
        import threading
        import time

        gate = threading.Event()
        written = []

        class Slow(RecordingActuator):
            blocking = True

            def set_demand(self, demand):
                gate.wait(2)  # a 2 s bus timeout, unless released
                written.append(demand)
                return demand

        heater = Slow(fresh("slow"))
        rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
        rig.loops[heater.name].regulate(50.0)
        t = time.monotonic()
        rig.on_read([sample(probe, temperature, 40.0, rig.clock.now_ns())])
        assert time.monotonic() - t < 0.2, "the delivery did not wait on the bus"
        assert written == [], "the write is queued"
        gate.set()
        deadline = time.monotonic() + 2
        while len(written) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert written and written[-1] == pytest.approx(60.0)
        assert rig.writers[heater.name].expected == pytest.approx(60.0)
        rig.stop()

    def test_newest_demand_wins_and_a_failing_bus_is_reported(self, rig, probe, temperature, fresh):
        import time

        class Flaky(RecordingActuator):
            blocking = True
            fail = True

            def set_demand(self, demand):
                if self.fail:
                    raise OSError("bus timeout")
                return super().set_demand(demand)

        heater = Flaky(fresh("flaky"))
        rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
        rig.loops[heater.name].regulate(50.0)
        rig.on_read([sample(probe, temperature, 40.0, rig.clock.now_ns())])
        writer = rig.writers[heater.name]
        deadline = time.monotonic() + 2
        while writer.failed is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert writer.failed is not None and writer.failed.kind == "write_failed"
        assert [(n, c.kind) for n, c in rig.write_conditions()] == [(heater.name, "write_failed")]
        assert rig.recent[-1].kind == "write_failed" and rig.recent[-1].subject == heater.name
        heater.fail = False
        rig.on_read([sample(probe, temperature, 41.0, rig.clock.now_ns(), seq=2)])
        deadline = time.monotonic() + 2
        while writer.failed is not None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert writer.failed is None and rig.recent[-1].kind == "write_recovered"
        rig.stop()

    def test_a_synchronous_actuator_is_written_in_the_tick(self, rig, probe, temperature, heater):
        rig.attach_loop(probe[temperature], heater, law=PI(kp=1.0))
        rig.loops[heater.name].regulate(50.0)
        rig.on_read([sample(probe, temperature, 40.0, rig.clock.now_ns())])
        assert heater.demands[-1] == pytest.approx(60.0) and rig.writers == {}
