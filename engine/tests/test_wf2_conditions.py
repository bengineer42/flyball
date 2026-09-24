"""Signal faults stage 1: event codes and severities, the condition store and its edges."""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from flyball.control.laws import P
from flyball.foundation.device import Code, Committable, Demand, Sample, Scope, Severity
from flyball.record.migrate import available
from flyball.record.sqlite import SqliteStore
from flyball.rig.stopping import Actor
from test_rig_devices import POWER, Furnace

# region Migration


def _store_at_0018(path, events: list[tuple[int, str | None, str, dict]]) -> None:
    """A database at schema 0018 with one session and `events` in the old shape."""
    connection = sqlite3.connect(path)
    files = available()
    for version in range(1, 19):
        connection.executescript("BEGIN;\n" + files[version].read_text() + "\nCOMMIT;")
    connection.executescript("""
        DELETE FROM schema_version;
        INSERT INTO schema_version (version) VALUES (18);
        INSERT INTO session (id, start_ns, end_ns, origin_ns) VALUES (1, 1000, 5000, 1000);
    """)
    connection.executemany(
        "INSERT INTO event (session_id, offset_ns, source, kind, detail) VALUES (1, ?, ?, ?, ?)",
        [(offset, source, kind, json.dumps(detail)) for offset, source, kind, detail in events],
    )
    connection.commit()
    connection.close()


def test_the_migration_renames_kind_to_code_and_level_to_a_severity_string(tmp_path):
    path = tmp_path / "old.db"
    _store_at_0018(
        path,
        [
            (10, "furnace", "offline", {"level": 40, "scope": "device", "message": "gone"}),
            (20, "bake", "step", {"level": 20, "scope": "program", "message": "one"}),
            (30, None, "note", {"x": 1}),
        ],
    )
    store = SqliteStore(path)
    events = store.events(1)
    assert [(e.code, e.source) for e in events] == [
        ("offline", "furnace"),
        ("step", "bake"),
        ("note", None),
    ]
    assert events[0].detail == {"severity": "error", "scope": "device", "message": "gone"}
    assert events[1].detail["severity"] == "info" and "level" not in events[1].detail
    assert events[2].detail == {"x": 1}, "a detail with no numeric level is left as it was"
    assert [e.code for e in store.events(1, code="offline")] == ["offline"]
    store.close()


def test_the_migration_turns_the_recovery_codes_into_cleared_edges(tmp_path):
    path = tmp_path / "old.db"
    device = {"level": 40, "scope": "device", "message": "m"}
    controller = {"level": 40, "scope": "controller", "message": "m"}
    program = {"level": 40, "scope": "program", "message": "m"}
    _store_at_0018(
        path,
        [
            (1, "pump", "write_failed", device),
            (2, "pump", "write_recovered", {**device, "level": 20}),
            (3, "pump", "commit_failed", device),
            (4, "pump", "commit_recovered", {**device, "level": 20}),
            (5, "pump.power", "step_failed", controller),
            (6, "pump.power", "step_recovered", {**controller, "level": 20}),
            (7, "bake[1]", "step_failed", program),
            (8, "pump.power", "limit_unknown", controller),
            (9, "pump.power", "limit_known", {**controller, "level": 20}),
            (10, "daq", "offline", device),
            (11, "daq", "restarted", {**device, "level": 20}),
            (12, "daq", "slow", device),
            (13, "bake", "started", program),
        ],
    )
    store = SqliteStore(path)
    assert [(e.code, e.edge) for e in store.events(1)] == [
        ("write_failed", "raised"),
        ("write_failed", "cleared"),
        ("write_failed", "raised"),  # 0022: commit_failed is write_failed now (A6)
        ("write_failed", "cleared"),
        ("step_failed", "raised"),
        ("step_failed", "cleared"),
        ("step_failed", None),  # a program's step: a point event
        ("limit_unknown", "raised"),
        ("limit_unknown", "cleared"),
        ("offline", "raised"),
        ("offline", "cleared"),
        ("slow", "raised"),
        ("started", None),
    ]
    store.close()


def test_a_severity_is_ranked_as_logging_ranks_it():
    assert [s.rank for s in Severity] == [10, 20, 30, 40]
    assert str(Severity.WARNING) == "warning"


def test_an_events_widget_level_is_migrated_to_its_severity():
    from flyball.interfaces.server.routes.dashboards import SCHEMA_VERSION, migrate

    v4 = {
        "schema_version": 4,
        "widgets": [
            {"id": "e", "kind": "events", "config": {"level": "WARNING", "limit": 5}},
            {"id": "r", "kind": "readout", "config": {"level": "kept"}},
        ],
    }
    migrated = migrate(v4)
    assert migrated["schema_version"] == SCHEMA_VERSION == 6
    assert migrated["widgets"][0]["config"] == {"severity": "warning", "limit": 5}
    assert migrated["widgets"][1]["config"] == {"level": "kept"}, "only the events widget"


# endregion

# region Wire


def test_an_event_on_the_wire_carries_a_code_and_a_lowercase_severity():
    from flyball.foundation.device import Code, Scope
    from flyball.interfaces.server.routes.events import event_out
    from flyball.rig import Rig

    rig = Rig("t")
    event = rig.event(Severity.WARNING, Scope.RIG, "t", Code.RESTORED, "hello")
    out = event_out(event)
    assert (out["code"], out["severity"]) == ("restored", "warning")
    assert "kind" not in out and "level" not in out


# endregion


# region The store: edges on transitions only


def _edges(rig, code) -> list:
    return [(e.edge, e.subject) for e in rig.recent if e.code == code]


class TestEdges:
    def test_a_set_raises_once_and_a_clear_clears_once_with_its_duration(self, rig, clock, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        assert rig.conditions.set(furnace, Code.SLOW, Severity.WARNING, "first") is True
        clock.advance(2.0)
        assert rig.conditions.set(furnace, Code.SLOW, Severity.WARNING, "second") is False
        (condition,) = rig.conditions.of(furnace)
        assert condition.message == "second", "a repeated set updates the message"
        assert condition.since_ns == clock.now_ns() - 2_000_000_000, "and keeps when it began"
        assert (condition.scope, condition.subject) == (Scope.DEVICE, furnace.name)
        assert _edges(rig, Code.SLOW) == [("raised", furnace.name)], "one edge, not one per set"
        clock.advance(3.0)
        cleared = rig.conditions.clear(furnace, Code.SLOW)
        assert cleared is not None and rig.conditions.of(furnace) == []
        assert rig.conditions.clear(furnace, Code.SLOW) is None, "nothing held: no edge"
        assert _edges(rig, Code.SLOW) == [("raised", furnace.name), ("cleared", furnace.name)]
        (last,) = [e for e in rig.recent if e.code == Code.SLOW and e.edge == "cleared"]
        assert last.details["duration_s"] == pytest.approx(5.0)
        assert last.severity is Severity.INFO

    def test_conditions_are_keyed_by_the_owner_object_not_its_name(self, rig, fresh):
        name = fresh("furnace")
        first = Furnace(name)
        rig.add_device(first)
        rig.conditions.set(first, Code.SLOW, Severity.WARNING, "old one")
        rig.remove_device(name)
        second = Furnace(name)
        rig.add_device(second)
        assert rig.conditions.of(second) == [], "a device re-added under the name starts clean"

    def test_a_removed_device_clears_its_conditions(self, rig, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        rig.conditions.set(furnace, Code.SLOW, Severity.WARNING, "slow")
        rig.conditions.set(furnace, Code.OFFLINE, Severity.ERROR, "gone")
        rig.remove_device(furnace.name)
        assert rig.conditions.all() == []
        cleared = [e for e in rig.recent if e.edge == "cleared" and e.subject == furnace.name]
        assert sorted(e.code for e in cleared) == ["offline", "slow"], "one cleared edge each"

    def test_a_detached_controller_clears_its_conditions(self, rig, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        controller = rig.attach_controller(
            furnace.signals["heater1"], furnace.signals["zone1"], law=P(kp=1.0)
        )
        rig.conditions.set(controller, Code.STALE_INPUT, Severity.WARNING, "held")
        rig.detach_controller(controller.name)
        assert rig.conditions.all() == []
        assert _edges(rig, Code.STALE_INPUT)[-1] == ("cleared", controller.name)


class TestSubscribers:
    def test_a_subscriber_gets_each_edge_off_the_rig_lock(self, rig, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        seen: list = []
        heard = threading.Event()
        main = threading.current_thread()

        def hear(edge) -> None:
            seen.append((
                edge.event.edge,
                edge.condition.code,
                edge.owner,
                threading.current_thread(),
            ))
            if len(seen) == 2:
                heard.set()

        unsubscribe = rig.conditions.subscribe(hear)
        with rig.lock:  # a producer under the lock, as a delivery is
            rig.conditions.set(furnace, Code.SLOW, Severity.WARNING, "slow")
            rig.conditions.clear(furnace, Code.SLOW)
            assert heard.wait(2.0), "delivered while the producer still holds the lock"
        assert [(e, c, o) for e, c, o, _ in seen] == [
            ("raised", "slow", furnace),
            ("cleared", "slow", furnace),
        ]
        assert all(thread is not main for *_, thread in seen)
        unsubscribe()
        rig.conditions.set(furnace, Code.SLOW, Severity.WARNING, "again")
        rig.conditions.flush(2.0)
        assert len(seen) == 2, "unsubscribed"

    def test_a_subscriber_that_raises_does_not_stop_the_others(self, rig, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        seen: list = []

        def bad(edge) -> None:
            raise RuntimeError("boom")

        rig.conditions.subscribe(bad)
        rig.conditions.subscribe(seen.append)
        rig.conditions.set(furnace, Code.SLOW, Severity.WARNING, "slow")
        rig.conditions.flush(2.0)
        assert len(seen) == 1


# endregion

# region Producers: the pairs are one condition each


class Flaky(Committable):
    out = Demand("out", "Out", POWER, limits=(0.0, 100.0))

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.fail = False

    def commit(self, time_ns: int) -> None:
        if self.fail:
            raise OSError("bus gone")
        super().commit(time_ns)


class TestProducers:
    def test_a_commit_failure_is_write_failed_raised_then_cleared(self, rig, clock, fresh):
        flaky = Flaky(fresh("flaky"))
        rig.add_device(flaky)
        out = flaky.signals["out"]
        flaky.fail = True
        for _ in range(3):
            with pytest.raises(OSError):
                rig.write(flaky.root, {out: 5.0})
        (held,) = rig.conditions.of(flaky)
        assert held.code == Code.WRITE_FAILED and held.severity is Severity.ERROR
        flaky.fail = False
        clock.advance(1.0)
        rig.write(flaky.root, {out: 5.0})
        assert rig.conditions.of(flaky) == []
        assert _edges(rig, Code.WRITE_FAILED) == [("raised", flaky.name), ("cleared", flaky.name)]
        assert not [e for e in rig.recent if e.code in ("commit_recovered", "commit_failed")]

    def test_an_offline_device_is_cleared_by_its_first_good_read(self, rig, clock, fresh):
        furnace = Furnace(fresh("furnace"))
        furnace.poll_s = 1.0
        rig.add_device(furnace)
        rig.start_polling(furnace)
        clock.advance(1.0)
        furnace.fail = True
        clock.advance(3.0)
        (offline,) = rig.conditions.of(furnace)
        assert offline.code == Code.OFFLINE and "modbus timeout" in offline.message
        furnace.fail = False
        rig.polling.restart(furnace.name)
        assert [c.code for c in rig.conditions.of(furnace)] == ["offline"], "not by the restart"
        clock.advance(1.0)
        assert rig.conditions.of(furnace) == []
        assert _edges(rig, Code.OFFLINE) == [("raised", furnace.name), ("cleared", furnace.name)]
        assert not [e for e in rig.recent if e.code == Code.RESTARTED], "restarted is the runner's"

    def test_a_stale_input_hold_is_a_condition_on_the_controller(self, rig, clock, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        zone1 = furnace.signals["zone1"]
        zone1.set_meta(stale_after_s=5.0)
        controller = rig.attach_controller(furnace.signals["heater1"], zone1, law=P(kp=1.0))
        controller.regulate(30.0)
        assert rig.hold_reason(controller) == Code.STALE_INPUT, "never read: stale"
        assert rig.hold_reason(controller) == Code.STALE_INPUT
        (held,) = rig.conditions.of(controller)
        assert (held.code, held.scope, held.subject) == (
            Code.STALE_INPUT,
            Scope.CONTROLLER,
            controller.name,
        )
        rig.on_samples([Sample(furnace.root, clock.now_ns(), {zone1: 20.0})])
        assert rig.conditions.of(controller) == []
        assert _edges(rig, Code.STALE_INPUT) == [
            ("raised", controller.name),
            ("cleared", controller.name),
        ]

    def test_a_law_error_is_raised_then_cleared(self, rig, clock, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        zone1 = furnace.signals["zone1"]
        controller = rig.attach_controller(furnace.signals["heater1"], zone1, law=P(kp=1.0))
        controller.regulate(30.0)
        broken = {"on": True}
        original = controller.on_reading

        def on_reading(reading):
            if broken["on"]:
                raise ValueError("bad law")
            return original(reading)

        controller.on_reading = on_reading  # type: ignore[method-assign]
        for value in (20.0, 21.0):
            clock.advance(1.0)
            rig.on_samples([Sample(furnace.root, clock.now_ns(), {zone1: value})])
        broken["on"] = False
        assert controller.mode.value == "manual", "a law error takes at least on_fault: manual"
        rig.stopping.reset(f"on_fault:{controller.name}", Actor("ben", "", "human", "http"))
        controller.regulate(30.0)
        clock.advance(1.0)
        rig.on_samples([Sample(furnace.root, clock.now_ns(), {zone1: 22.0})])
        assert _edges(rig, Code.STEP_FAILED) == [
            ("raised", controller.name),
            ("cleared", controller.name),
        ]
        assert not [e for e in rig.recent if e.code == "step_recovered"]


# endregion

# region Recorded, and on the wire


def test_a_session_records_when_a_condition_started_and_cleared(rig, clock, fresh, tmp_path):
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    store = SqliteStore(tmp_path / "s.db")
    recorder = rig.start_recording(store)
    clock.advance(1.0)
    rig.conditions.set(furnace, Code.SLOW, Severity.WARNING, "slow")
    clock.advance(4.0)
    rig.conditions.clear(furnace, Code.SLOW)
    rig.stop_recording()
    events = [e for e in store.events(recorder.writer.session.id) if e.code == "slow"]
    assert [(e.edge, e.offset_ns, e.source) for e in events] == [
        ("raised", 1_000_000_000, furnace.name),
        ("cleared", 5_000_000_000, furnace.name),
    ]
    assert events[0].detail["severity"] == "warning"
    assert events[1].detail["details"]["duration_s"] == pytest.approx(4.0)
    store.close()


def test_a_failed_recording_is_a_condition_on_the_rig_until_the_next_starts(rig, tmp_path):
    rig._recording_failed(OSError("disk full"))
    (failed,) = rig.conditions.of(rig)
    assert (failed.code, failed.scope) == (Code.RECORDING_FAILED, Scope.RIG)
    store = SqliteStore(tmp_path / "s.db")
    rig.start_recording(store)
    assert rig.conditions.of(rig) == []
    assert [e.edge for e in rig.recent if e.code == Code.RECORDING_FAILED] == ["raised", "cleared"]
    rig.stop_recording()
    store.close()


def test_health_conditions_come_from_the_store_with_scope_and_subject(rig, fresh):
    from conftest import TestClient
    from flyball.interfaces.server import create_app, set_rig

    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    controller = rig.attach_controller(
        furnace.signals["heater1"], furnace.signals["zone1"], law=P(kp=1.0)
    )
    rig.conditions.set(controller, Code.STALE_INPUT, Severity.WARNING, "held")
    rig.conditions.set(furnace, Code.OFFLINE, Severity.ERROR, "gone")
    set_rig(rig)
    try:
        with TestClient(create_app()) as client:
            body = client.get("/api/health").json()
    finally:
        set_rig(None)
    held = [(c["scope"], c["subject"], c["code"], c["severity"]) for c in body["conditions"]]
    assert held == [
        ("controller", controller.name, "stale_input", "warning"),
        ("device", furnace.name, "offline", "error"),
    ]
    assert body["ok"] is False, "a fault condition at error"
    assert body["alarms"]["alarm"] == 0 and body["alarms"]["warn"] == 0, "faults are not alarms"


# endregion

# region `slow`, de-flapped


class Paced(Furnace):
    """A furnace whose reads take what `took` says, in the rig's time, one per read."""

    def __init__(self, name: str, rig) -> None:
        super().__init__(name)
        self.rig = rig
        self.took: list[float] = []

    def read(self, time_ns, node=None):
        if self.took:
            self.rig.clock.sleep(self.took.pop(0))
        return super().read(time_ns, node)


def _paced(rig, fresh) -> Paced:
    paced = Paced(fresh("paced"), rig)
    paced.poll_s = 1.0
    rig.add_device(paced)
    rig.start_polling(paced)
    rig.polling.stop_all()  # reads are driven by hand, one `_read` each
    return paced


def _reads(rig, paced: Paced, took: list[float]) -> None:
    paced.took = list(took)
    for _ in took:
        rig.polling._read(paced)


class TestSlow:
    def test_a_flapping_read_raises_nothing(self, rig, fresh):
        paced = _paced(rig, fresh)
        _reads(rig, paced, [1.5, 0.1, 1.5, 1.5, 0.1, 1.5, 0.1] * 3)
        assert _edges(rig, Code.SLOW) == [], "never three over the period in a row"
        run = rig.polling.run(paced.name)
        assert run.missed == 12 and run.read_s == pytest.approx(0.1)

    def test_raised_after_three_over_and_cleared_after_five_well_under(self, rig, fresh):
        paced = _paced(rig, fresh)
        _reads(rig, paced, [1.5, 1.5])
        assert _edges(rig, Code.SLOW) == []
        _reads(rig, paced, [1.5])
        assert _edges(rig, Code.SLOW) == [("raised", paced.name)]
        _reads(rig, paced, [1.5, 2.0, 0.1, 0.1, 0.1, 0.1])
        assert _edges(rig, Code.SLOW) == [("raised", paced.name)], "one edge, not one per read"
        _reads(rig, paced, [0.9])  # under the period, over 0.8 of it: starts the count again
        _reads(rig, paced, [0.1, 0.1, 0.1, 0.1])
        assert [c.code for c in rig.conditions.of(paced)] == ["slow"]
        _reads(rig, paced, [0.1])
        assert _edges(rig, Code.SLOW) == [("raised", paced.name), ("cleared", paced.name)]

    def test_only_the_read_is_timed_not_the_delivery(self, rig, fresh):
        paced = _paced(rig, fresh)
        real = rig.on_samples

        def slow_delivery(samples):
            rig.clock.sleep(5.0)  # a controller, the recorder, the lock: not the device's
            real(samples)

        rig.on_samples = slow_delivery  # type: ignore[method-assign]
        _reads(rig, paced, [0.1, 0.1, 0.1, 0.1])
        assert _edges(rig, Code.SLOW) == [] and rig.polling.run(paced.name).missed == 0

    def test_the_run_on_the_wire_carries_read_s_and_missed(self, rig, fresh):
        from flyball.interfaces.server.schemas import RunOut

        paced = _paced(rig, fresh)
        _reads(rig, paced, [1.5, 0.25])
        out = RunOut.of(rig.polling.run(paced.name)).model_dump()
        assert out["read_s"] == pytest.approx(0.25) and out["missed"] == 1


# endregion

# region Driver-reported conditions


class TestDriverConditions:
    def test_a_device_has_no_conditions_signal(self, fresh):
        furnace = Furnace(fresh("furnace"))
        assert "conditions" not in furnace.signals

    def test_a_driver_raises_and_clears_through_the_rig_store(self, rig, clock, fresh):
        furnace = Furnace(fresh("furnace"))
        rig.add_device(furnace)
        zone1 = furnace.signals["zone1"]
        assert furnace.set_condition("railed", Severity.WARNING, "at the power limit") is True
        furnace.set_condition("broken", Severity.ERROR, "open circuit", signal=zone1)
        assert [(c.code, c.scope, c.subject) for c in rig.conditions.all()] == [
            ("railed", Scope.DEVICE, furnace.name),
            ("broken", Scope.SIGNAL, zone1.address),
        ]
        assert [c.code for c in furnace.held_conditions()] == ["railed", "broken"]
        clock.advance(2.0)
        furnace.clear_condition("broken", signal=zone1)
        assert _edges(rig, "broken") == [("raised", zone1.address), ("cleared", zone1.address)]
        rig.remove_device(furnace.name)
        assert _edges(rig, "railed") == [("raised", furnace.name), ("cleared", furnace.name)]
        assert furnace.held_conditions() == [], "a removed device keeps nothing of the rig's"

    def test_what_a_driver_raised_before_it_was_added_is_taken_into_the_rig(self, rig, fresh):
        furnace = Furnace(fresh("furnace"))
        furnace.set_condition("waiting", Severity.INFO, "warming up")
        assert rig.conditions.all() == []
        rig.add_device(furnace)
        (held,) = rig.conditions.of(furnace)
        assert held.code == "waiting" and _edges(rig, "waiting") == [("raised", furnace.name)]
        furnace.clear_condition("waiting")
        assert rig.conditions.all() == []

    def test_the_sim_s_broken_sensor_is_a_condition_on_its_signal(self, rig, clock):
        from flyball_sim import Lag, SimDaq
        from flyball_sim.devices import DaqPort

        port = DaqPort(port="output", quantity="temperature", unit="°C")
        daq = SimDaq("daq", Lag(tau_s=10.0, value=20.0), {"t": port})
        rig.add_device(daq)
        daq.fail("t")
        (broken,) = rig.conditions.all()
        assert (broken.code, broken.scope, broken.subject) == ("broken", Scope.SIGNAL, "daq.t")
        daq.restore("t")
        assert rig.conditions.all() == []


# endregion
