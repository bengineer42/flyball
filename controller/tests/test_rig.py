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
    with pytest.raises(OSError):
        rig.readers._read(reader)
    run = rig.readers.run(reader.name)
    assert run.running is False
    assert run.conditions[0].kind == "offline" and run.conditions[0].level is Level.ERROR
    assert "I2C timeout" in run.conditions[0].message


def test_function_reader_stamps_every_source_with_one_instant(fresh, temperature):
    a, b = Source(fresh("a"), (temperature,)), Source(fresh("b"), (temperature,))
    reader = FunctionReader(
        fresh("sim"), {a: lambda t: {temperature: 1.0}, b: lambda t: {temperature: 2.0}}
    )
    samples = list(reader.read(123))
    assert [s.time_ns for s in samples] == [123, 123]
    assert {s.source: s.values[temperature] for s in samples} == {a: 1.0, b: 2.0}
    assert reader.channels == {a[temperature], b[temperature]}
