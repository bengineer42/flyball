"""Recording: signal-keyed, buffered, batched, complete on close; the store and its migration."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from enum import StrEnum

import pytest

from flyball.control import PI
from flyball.foundation.device import (
    Access,
    Committable,
    Device,
    Node,
    Readable,
    Role,
    Sample,
    SignalSpec,
    WriteState,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Watt
from flyball.record import Downsample, NotDeclaredError, SqliteStore, Window
from flyball.record.migrate import available
from flyball.runtime.recorder import Recorder

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)


class Furnace(Readable, Committable):
    """RP zones, a W heater and an RW setpoint on one flat tree."""

    TREE = (
        SignalSpec(name="zone1", quantity=TEMP, access=Access.RP, range=(0.0, 1200.0)),
        SignalSpec(name="zone2", quantity=TEMP, access=Access.RP),
        SignalSpec(
            name="heater",
            quantity=POWER,
            access=Access.RPW,
            role=Role.DEMAND,
            limits=(0.0, 2500.0),
            label="Heater",
        ),
        SignalSpec(name="setpoint", quantity=TEMP, access=Access.RW, role=Role.DEMAND),
    )

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.temps = {self.signals["zone1"]: 20.0, self.signals["zone2"]: 20.0}

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        yield Sample(self.root, time_ns, dict(self.temps))


@pytest.fixture
def furnace(rig, fresh):
    furnace = Furnace(fresh("furnace"), label="Furnace A")
    rig.add_device(furnace)
    return furnace


def _sample(furnace: Furnace, time_ns: int, value: float, **more: float) -> Sample:
    values = {furnace.signals["zone1"]: value}
    values.update({furnace.signals[name]: v for name, v in more.items()})
    return Sample(furnace.root, time_ns, values)


# region Through the rig


def test_deliveries_are_buffered_and_written_on_close(rig, furnace, clock):
    controller = rig.attach_controller(
        furnace.signals["heater"], furnace.signals["zone1"], law=PI(kp=1.0)
    )
    controller.regulate(10.0)
    store = SqliteStore(":memory:")
    recorder = rig.start_recording(store)
    for i in range(50):
        clock.advance(0.01)
        rig.on_samples([_sample(furnace, clock.now_ns(), float(i))])
    assert len(recorder._ticks) == 50, "nothing flushed inside the interval"
    rig.stop_recording()
    session = store.sessions()[0]
    assert len(store.ticks(session.id, controller.name)) == 50
    assert len(store.series(session.id, f"{furnace.name}.zone1").points) == 50
    # The controller's target committed once per delivery: one write state each.
    assert len(store.write_states(session.id, f"{furnace.name}.heater")) == 50


def test_declarations_carry_the_device_and_signal_metadata(rig, furnace, clock):
    controller = rig.attach_controller(
        furnace.signals["heater"], furnace.signals["zone1"], law=PI(kp=1.0)
    )
    store = SqliteStore(":memory:")
    rig.start_recording(store)
    rig.on_samples([_sample(furnace, clock.now_ns(), 1.0)])
    rig.stop_recording()
    session = store.sessions()[0]
    (device,) = store.devices(session.id)
    assert (device.address, device.driver, device.label) == (furnace.name, "Furnace", "Furnace A")
    assert device.config == {"link": None}
    signals = {s.address: s for s in store.signals(session.id)}
    assert set(signals) == {
        f"{furnace.name}.{n}" for n in ("conditions", "zone1", "zone2", "heater", "setpoint")
    }, "by default everything that publishes or can be written"
    zone1 = signals[f"{furnace.name}.zone1"]
    assert (zone1.quantity, zone1.unit, zone1.access, zone1.range) == (
        "temperature",
        "°C",
        "rp",
        (0.0, 1200.0),
    )
    assert zone1.device == furnace.name and zone1.dtype == "float" and zone1.shape == []
    heater = signals[f"{furnace.name}.heater"]
    assert (heater.access, heater.limits, heater.label) == ("rpw", (0.0, 2500.0), "Heater")
    heater_write, setpoint_write = store.writes(session.id)
    assert heater_write.address == heater.address and heater_write.limits == (0.0, 2500.0)
    assert setpoint_write.signal.access == "rw" and setpoint_write.limits is None
    (row,) = store.controllers(session.id)
    assert row.name == controller.name == heater.address
    assert row.source == zone1.address and row.law["tag"] == "PI" and row.feedforward is not None


def test_a_manual_demand_is_a_write_state_row(rig, furnace, clock):
    store = SqliteStore(":memory:")
    rig.start_recording(store)
    clock.advance(1.0)
    rig.demand(furnace.root, {"heater": 3000.0, "setpoint": 200.0})
    rig.stop_recording()
    session = store.sessions()[0]
    (state,) = store.write_states(session.id, f"{furnace.name}.heater")
    assert state.offset_ns == 1_000_000_000
    assert (state.value, state.requested, state.at_limit, state.controller) == (
        2500.0,
        3000.0,
        "high",
        None,
    )
    # A write to an RW setting is in the history as what was set...
    (setting,) = store.write_states(session.id, f"{furnace.name}.setpoint")
    assert setting.value == 200.0 and setting.requested is None
    # ...but a fresh read of it is not a reading.
    assert store.series(session.id, f"{furnace.name}.setpoint").points == ()


def test_samples_are_keyed_by_device_and_node_with_seq_per_device(rig, furnace, clock):
    store = SqliteStore(":memory:")
    rig.start_recording(store, signals=[furnace.signals["zone1"], furnace.signals["zone2"]])
    clock.advance(1.0)
    rig.on_samples([_sample(furnace, clock.now_ns(), 20.5, zone2=30.0)])
    clock.advance(1.0)
    rig.on_samples([_sample(furnace, clock.now_ns(), 21.0)])
    rig.stop_recording()
    session = store.sessions()[0]
    rows = store.samples(session.id, furnace.name)
    assert [(r.seq, r.offset_ns, r.node) for r in rows] == [
        (1, 1_000_000_000, furnace.name),
        (2, 2_000_000_000, furnace.name),
    ]
    assert rows[0].values == {f"{furnace.name}.zone1": 20.5, f"{furnace.name}.zone2": 30.0}
    assert rows[1].values == {f"{furnace.name}.zone1": 21.0}
    windowed = store.samples(session.id, furnace.name, Window(start_ns=1_500_000_000))
    assert [r.seq for r in windowed] == [2]
    assert store.samples(session.id, f"{furnace.name}.zone2") == [], (
        "a node address selects the samples on it; none were on a namespace"
    )


def test_the_writer_thread_flushes_off_the_delivery_path(rig, furnace, clock):
    rig.attach_controller(furnace.signals["heater"], furnace.signals["zone1"], law=PI(kp=1.0))
    store = SqliteStore(":memory:")
    recorder = rig.start_recording(store)
    recorder.flush_s = 0.01
    rig.on_samples([_sample(furnace, clock.now_ns(), 1.0)])
    assert recorder._samples, "buffered on delivery, not written"
    deadline = time.monotonic() + 2
    while recorder._samples and time.monotonic() < deadline:
        time.sleep(0.005)
    assert recorder._samples == [] and recorder._ticks == [], "the thread wrote it"
    assert recorder.running
    rig.stop_recording()
    assert not recorder.running


def test_a_failing_store_stops_recording_and_raises_an_event(rig, furnace, clock):
    store = SqliteStore(":memory:")
    recorder = rig.start_recording(store, signals=[furnace.signals["zone1"]])
    recorder.flush_s = 0.01

    class Broken:  # a session writer whose disk has filled
        def __init__(self, inner):
            self._inner = inner

        def write_samples(self, samples):
            raise OSError("disk full")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    recorder.writer = Broken(recorder.writer)  # type: ignore[assignment]
    rig.on_samples([_sample(furnace, clock.now_ns(), 1.0)])
    deadline = time.monotonic() + 2
    while recorder.failed is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert isinstance(recorder.failed, OSError)
    assert rig.recorder is None, "detached: control goes on unrecorded"
    assert rig.recent[-1].kind == "recording_failed" and "disk full" in rig.recent[-1].message
    rig.on_samples([_sample(furnace, clock.now_ns(), 2.0)])  # still delivers


# endregion

# region The recorder on its own


class TestRecorder:
    def test_only_publishing_values_are_kept_from_a_sample(self, furnace):
        store = SqliteStore(":memory:")
        writer = store.open_session(1_000)
        recorder = Recorder(writer, [furnace.signals["zone1"], furnace.signals["setpoint"]])
        recorder.record([_sample(furnace, 2_000, 20.0, setpoint=150.0)], (), {})
        (kept,) = recorder._samples
        assert kept.values == {furnace.signals["zone1"]: 20.0}, "the RW setting's read is dropped"
        recorder.record([Sample(furnace.root, 3_000, {furnace.signals["setpoint"]: 1.0})], (), {})
        assert len(recorder._samples) == 1, "a sample with nothing to keep is dropped whole"
        recorder.close(4_000)
        session = writer.session
        assert [p.value for p in store.series(session.id, f"{furnace.name}.zone1").points] == [20.0]
        assert store.samples(session.id, furnace.name)[0].values == {f"{furnace.name}.zone1": 20.0}

    def test_write_states_take_the_commit_time(self, furnace):
        store = SqliteStore(":memory:")
        writer = store.open_session(1_000)
        heater = furnace.signals["heater"]
        recorder = Recorder(writer, [heater])
        recorder.record((), (), {heater: WriteState(value=5.0)}, time_ns=1_500)
        recorder.record([_sample(furnace, 2_000, 20.0)], (), {heater: WriteState(value=6.0)})
        recorder.record((), (), {heater: WriteState(value=7.0)})  # no time: the last seen
        recorder.close(3_000)
        rows = store.write_states(writer.session.id, heater.address)
        assert [(r.offset_ns, r.value) for r in rows] == [(500, 5.0), (1000, 7.0)], (
            "two states at one instant: the later one stands"
        )

    def test_the_writer_refuses_what_was_not_declared(self, furnace):
        store = SqliteStore(":memory:")
        writer = store.open_session(1_000)
        with pytest.raises(NotDeclaredError, match="device"):
            writer.declare_signal(furnace.signals["zone1"])
        writer.declare_device(furnace)
        writer.declare_signal(furnace.signals["zone1"])
        with pytest.raises(NotDeclaredError, match="signal"):
            writer.write_samples([_sample(furnace, 2_000, 1.0, zone2=2.0)])
        with pytest.raises(NotDeclaredError, match="write"):
            writer.write_states(0, {furnace.signals["heater"]: WriteState(value=1.0)})
        with pytest.raises(NotDeclaredError, match="signal"):
            store.series(writer.session.id, f"{furnace.name}.zone2")


# endregion

# region Non-float dtypes


class Mode(StrEnum):
    IDLE = "idle"
    RUN = "run"


class Recipe(Device):
    """One RP enum-valued signal and one RP json-valued (dict) signal."""

    TREE = (
        SignalSpec(name="mode", quantity=TEMP, access=Access.RP, vtype=Mode),
        SignalSpec(name="config", quantity=TEMP, access=Access.RP, vtype=dict),
    )


class TestNonFloatReadings:
    def test_enum_and_json_values_round_trip_through_samples_and_series(self, fresh):
        recipe = Recipe(fresh("recipe"))
        mode, config = recipe.signals["mode"], recipe.signals["config"]
        store = SqliteStore(":memory:")
        writer = store.open_session(1_000)
        writer.declare_device(recipe)
        writer.declare_signal(mode)
        writer.declare_signal(config)
        writer.write_samples([
            Sample(recipe.root, 2_000, {mode: Mode.RUN, config: {"kp": 1.0, "tags": ["a", "b"]}})
        ])
        session_id = writer.session.id

        series = store.series(session_id, mode.address)
        assert [p.value for p in series.points] == ["run"], "the wire value, not the member"

        rows = store.samples(session_id, recipe.name)
        assert rows[0].values == {
            mode.address: "run",
            config.address: {"kp": 1.0, "tags": ["a", "b"]},
        }

    def test_a_downsample_does_not_apply_to_a_non_float_series(self, fresh):
        recipe = Recipe(fresh("recipe"))
        mode = recipe.signals["mode"]
        store = SqliteStore(":memory:")
        writer = store.open_session(1_000)
        writer.declare_device(recipe)
        writer.declare_signal(mode)
        writer.write_samples([Sample(recipe.root, 2_000, {mode: Mode.IDLE})])
        series = store.series(writer.session.id, mode.address, downsample=Downsample(every=2))
        assert [p.value for p in series.points] == ["idle"], "every change, as it was"
        assert series.downsample is None


# endregion

# region Migration


def _legacy_store(path) -> None:
    """A database at schema 0006 with one session of the old shape, as a runner left it."""
    connection = sqlite3.connect(path)
    files = available()
    for version in range(1, 7):
        connection.executescript("BEGIN;\n" + files[version].read_text() + "\nCOMMIT;")
    connection.executescript("""
        DELETE FROM schema_version;
        INSERT INTO schema_version (version) VALUES (6);
        INSERT INTO session (id, start_ns, end_ns) VALUES (1, 1000, 5000);
        INSERT INTO measurand (session_id, id, name, unit, label)
            VALUES (1, 1, 'temperature', '°C', 'Temp'), (1, 2, 'power', 'W', NULL);
        INSERT INTO source (session_id, id, name, kind, label)
            VALUES (1, 1, 'probe', 'sht4x', 'Probe A'), (1, 2, 'meter', NULL, NULL);
        INSERT INTO channel VALUES (1, 1, 1), (1, 2, 2);
        INSERT INTO sample VALUES (1, 1, 1, 10), (1, 1, 2, 20), (1, 2, 1, 10);
        INSERT INTO reading VALUES (1, 1, 1, 1, 10, 20.5), (1, 1, 2, 1, 20, 21.0),
                                   (1, 2, 1, 2, 10, 100.0);
        INSERT INTO actuator VALUES (1, 'heater', 'Heater', '{"max_w": 10}'),
                                    (1, 'probe', 'Clash', NULL);
        INSERT INTO loop VALUES (1, 'heater', 1, 1, '{"tag":"PI"}', '{"tag":"none"}');
        INSERT INTO tick VALUES (1, 'heater', 20, 'regulating', 21.0, 25.0, 5.0, 5.0, NULL, NULL);
        INSERT INTO event (session_id, offset_ns, kind, detail) VALUES (1, 15, 'note', '{}');
        INSERT INTO tuning (name, law, config, created_ns, session_id, loop)
            VALUES ('warm', 'PI', '{"kp": 1}', 1, 1, 'heater');
    """)
    connection.close()


def test_migration_maps_a_0006_session_onto_the_device_model(tmp_path):
    path = tmp_path / "old.db"
    _legacy_store(path)
    store = SqliteStore(path)
    devices = store.devices(1)
    assert [(d.id, d.address, d.driver, d.config, d.label) for d in devices] == [
        (1, "probe", "sht4x", None, "Probe A"),
        (2, "meter", None, None, None),
        (3, "heater", "Heater", {"max_w": 10}, None),  # the clashing actuator was dropped
    ]
    signals = store.signals(1)
    assert [(s.address, s.quantity, s.unit, s.access, s.label) for s in signals] == [
        ("probe.temperature", "temperature", "°C", "rp", "Temp"),
        ("meter.power", "power", "W", "rp", None),
    ]
    assert [p.value for p in store.series(1, "probe.temperature").points] == [20.5, 21.0]
    assert store.samples(1, "meter")[0].values == {"meter.power": 100.0}
    (controller,) = store.controllers(1)
    assert (controller.name, controller.source, controller.law) == (
        "heater",
        "probe.temperature",
        {"tag": "PI"},
    )
    (tick,) = store.ticks(1, "heater")
    assert (tick.controller, tick.offset_ns, tick.setpoint) == ("heater", 20, 25.0)
    assert store.writes(1) == [] and store.events(1)[0].kind == "note"
    assert store.tuning("warm").controller == "heater"
    # The new tables work on the migrated database, and a delete cascades through them.
    store.delete_session(1)
    assert store.sessions() == []
    connection = sqlite3.connect(path)
    for table in ("device", "signal", "sample", "reading", "controller", "tick"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,), table
    connection.close()


# endregion
