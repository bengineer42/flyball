"""Signal faults stage 4: the value gate, `NoValue`, the store's `flag`, and A6.

A reading may have no value (`invalid`, `not_applicable`, `stale`): nothing
downstream substitutes a number for it. A controller freezes, a limit fails
closed, a settle wait resets, a band is unknown (A4), the store keeps a NULL
with its code (A2), and an echo demand whose device's writes fail reads
`stale(write_failed)` (A6).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator

import pytest

from conftest import TestClient
from flyball.control.laws import PI
from flyball.foundation.device import (
    Access,
    Code,
    Committable,
    Demand,
    Input,
    Limit,
    LimitNotKnownError,
    Node,
    NoValue,
    NoValueError,
    OnNoValue,
    Quality,
    Readable,
    Readback,
    Readout,
    Role,
    Sample,
    Severity,
    Signal,
    SignalSpec,
    invalid,
    normalised,
    not_applicable,
    railed,
)
from flyball.foundation.errors import NotReadyError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent, Watt
from flyball.interfaces.server import create_app, set_rig
from flyball.model.law import Transfer
from flyball.record import Downsample, Flag
from flyball.record.sqlite import SqliteStore
from flyball.rig import Rig
from flyball.runtime import stats
from flyball.sequencing.activities import Settled

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)


class Oven(Readable, Committable):
    """A zone read by polling, a heater and a fan (echo demands), a sensed valve, a setting."""

    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP, range=(0.0, 500.0)),
        SignalSpec(name="heater", quantity=POWER, role=Role.DEMAND, access=Access.RPW),
        SignalSpec(name="fan", quantity=POWER, role=Role.DEMAND, access=Access.RPW),
        SignalSpec(
            name="valve",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            readback=Readback.SENSED,
        ),
        SignalSpec(name="mode", quantity=POWER, role=Role.SETTING, access=Access.RP),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.zone: object = 20.0
        self.fail_reads = False
        self.fail_commits = False
        self.written: dict[str, float] = {}

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        if self.fail_reads:
            raise OSError("bus timeout")
        yield Sample(
            self.root,
            time_ns,
            {self.signals["zone"]: self.zone, self.signals["valve"]: 1.0},
        )

    def commit(self, time_ns: int) -> None:
        if self.fail_commits:
            raise OSError("bus timeout")
        super().commit(time_ns)

    def write_signal(self, signal: Signal, value: float) -> None:
        self.written[signal.name] = value


class Blocking(Oven):
    blocking = True


class Supplied(Committable):
    supply = Readout("supply", "Supply humidity", HUMIDITY)
    target = Demand("target", "Target humidity", HUMIDITY, limits=(0.0, supply))


class Follower(Committable):
    wet = Input("wet", "Wet supply", HUMIDITY)
    out = Demand("out", "Out", HUMIDITY)


@pytest.fixture
def oven(rig: Rig, fresh) -> Oven:
    oven = Oven(fresh("oven"))
    rig.add_device(oven)
    return oven


def _push(rig: Rig, signal: Signal, value: object) -> None:
    rig.on_samples([Sample(signal.node, rig.clock.now_ns(), {signal: value})])


def _latest(rig: Rig, signal: Signal) -> object:
    return rig.router.latest[signal].value


# region The gate


class TestTheValueGate:
    def test_none_nan_and_infinities_become_invalid_and_railed_a_mark(self, rig, oven):
        zone = oven.signals["zone"]
        for bad, reason in (
            (None, "no value"),
            (math.nan, "not finite"),
            (-math.inf, "not finite"),
        ):
            _push(rig, zone, bad)
            assert _latest(rig, zone) == invalid(reason)
        _push(rig, zone, railed(500.0, "high"))
        reading = rig.router.latest[zone]
        assert reading.value == 500.0 and reading.at_limit is Limit.HIGH and reading.usable

    def test_a_sample_with_nothing_to_gate_passes_as_itself(self, oven):
        sample = Sample(oven.root, 0, {oven.signals["zone"]: 21.0})
        assert normalised(sample) is sample

    def test_a_no_value_has_no_truth_value_and_is_never_ok_or_pending(self):
        with pytest.raises(TypeError):
            bool(invalid())
        with pytest.raises(ValueError):
            NoValue(Quality.PENDING)
        assert not_applicable("no_flow").quality is Quality.NOT_APPLICABLE
        assert not Quality.NOT_APPLICABLE.fault and Quality.INVALID.fault

    def test_value_raises_and_last_usable_keeps_the_last_number(self, rig, oven):
        zone = oven.signals["zone"]
        _push(rig, zone, 21.0)
        _push(rig, zone, invalid("crc"))
        with pytest.raises(NoValueError) as raised:
            _ = zone.value
        assert isinstance(raised.value, NotReadyError) and "invalid: crc" in str(raised.value)
        assert rig.router.last_usable[zone].value == 21.0
        reading = rig.router.latest[zone]
        assert not reading.usable and reading.quality is Quality.INVALID and reading.reason == "crc"

    def test_without_a_rig_a_device_gates_its_own_pushes(self, fresh):
        oven = Oven(fresh("oven"))
        oven.signals["zone"].push(math.nan)
        assert oven.router.latest[oven.signals["zone"]].value == invalid("not finite")

    def test_statistics_leave_out_readings_with_no_value(self, rig, oven):
        zone = oven.signals["zone"]
        for i in range(10):
            rig.clock.advance(1.0)
            _push(rig, zone, invalid() if i % 3 == 0 else float(i))
        readings = rig.recent_readings(zone)
        assert stats.rate(readings) is not None and stats.noise(readings, window=3) is not None


# endregion

# region Consumers


class TestNothingSubstitutes:
    def test_a_limit_following_a_no_value_fails_closed(self, rig, fresh):
        dev = Supplied(fresh("supplied"))
        rig.add_device(dev)
        _push(rig, dev.signals["supply"], 90.0)
        rig.write(dev.root, {"target": 95.0})
        assert dev.written[dev.signals["target"]].value == 90.0
        _push(rig, dev.signals["supply"], invalid("ne43_low", "low"))
        with pytest.raises(LimitNotKnownError):
            rig.write(dev.root, {"target": 50.0})

    def test_an_input_on_a_no_value_raises_and_is_pending_before_its_first(self, rig, oven, fresh):
        follower = Follower(fresh("follower"))
        rig.add_device(follower)
        rig.bind_inputs(follower, {"wet": oven.signals["zone"].address})
        with pytest.raises(NotReadyError, match="has not been read yet"):
            _ = follower.wet.value
        assert follower.wet.quality is Quality.PENDING
        _push(rig, oven.signals["zone"], 70.0)
        assert follower.wet.value == 70.0
        _push(rig, oven.signals["zone"], invalid())
        with pytest.raises(NoValueError):
            _ = follower.wet.value

    def test_a_railed_or_no_value_reading_resets_a_settle_count(self, rig, oven):
        zone, heater = oven.signals["zone"], oven.signals["heater"]
        _push(rig, zone, 30.0)
        controller = rig.attach_controller(heater, zone, law=PI(kp=1.0, ki=0.1))
        controller.regulate(30.0, transfer=Transfer.COLD)
        settle = Settled([controller], within=1.0, count=2)
        settle.attach(rig)
        for value in (30.0, invalid(), 30.0, railed(30.0, "high"), 30.0):
            rig.clock.advance(1.0)
            _push(rig, zone, value)
        assert not settle.fired
        rig.clock.advance(1.0)
        _push(rig, zone, 30.0)
        assert settle.fired


class TestAControllerFreezes:
    def _run(self, rig: Rig, oven: Oven, values: list[object]) -> tuple[PI, list[float | None]]:
        zone, heater = oven.signals["zone"], oven.signals["heater"]
        law = PI(kp=1.0, ki=0.5, tt=2.0)
        _push(rig, zone, 20.0)
        controller = rig.attach_controller(heater, zone, law=law)
        controller.regulate(30.0, transfer=Transfer.COLD)
        outputs = []
        for value in values:
            rig.clock.advance(1.0)
            _push(rig, zone, value)
            outputs.append(oven.written.get("heater"))
        return law, outputs

    def test_the_law_never_sees_a_no_value_and_resumes_after_three(self, rig, oven):
        law, outputs = self._run(rig, oven, [20.0, 20.0, invalid("crc")])
        integral = law.integral
        frozen_at = outputs[-1]
        (frozen,) = [c for c in rig.conditions.all() if c.code == Code.FROZEN]
        assert frozen.severity is Severity.WARNING and frozen.details["quality"] == "invalid"
        for _ in range(2):
            rig.clock.advance(1.0)
            _push(rig, oven.signals["zone"], 20.0)
            assert law.integral == integral and oven.written["heater"] == frozen_at
        rig.clock.advance(1.0)
        _push(rig, oven.signals["zone"], 20.0)
        assert law.integral != integral, "the third reading with a value steps it again"
        assert [e.edge for e in rig.recent if e.code == Code.FROZEN] == ["raised", "cleared"]

    def test_a_benign_no_value_freezes_at_info(self, rig, oven):
        self._run(rig, oven, [not_applicable("warming")])
        (frozen,) = [c for c in rig.conditions.all() if c.code == Code.FROZEN]
        assert frozen.severity is Severity.INFO

    def test_regulate_from_measured_on_a_no_value_is_refused(self, rig, oven):
        zone, heater = oven.signals["zone"], oven.signals["heater"]
        _push(rig, zone, 20.0)
        controller = rig.attach_controller(heater, zone, law=PI(kp=1.0, ki=0.5))
        _push(rig, zone, invalid())
        assert controller.last_value is None
        from flyball.model.controller import ValueSource
        from flyball.model.errors import LastReadingNotAvailableError

        with pytest.raises(LastReadingNotAvailableError):
            controller.regulate(ValueSource.MEASURED)

    def test_without_a_rig_the_law_is_not_called_on_a_no_value(self, oven):
        from flyball.foundation import Clock
        from flyball.foundation.device import Reading
        from flyball.model.controller import Controller

        law = PI(kp=1.0, ki=0.5)
        controller = Controller(Clock(), oven.signals["heater"], oven.signals["zone"], law=law)
        controller.regulate(30.0, transfer=Transfer.COLD)
        controller.on_reading(Reading(oven.signals["zone"], 1, invalid()))
        assert controller.held == Code.FROZEN


# endregion

# region Bands (A4)


class TestBandsOnNoValue:
    @staticmethod
    def _banded(rig: Rig, oven: Oven, **meta: object) -> Signal:
        zone = oven.signals["zone"]
        zone.set_meta(**meta)
        return zone

    def test_an_alarm_band_fires_band_unknown_after_its_grace_and_counts_unknown(self, rig, oven):
        zone = self._banded(rig, oven, alarm=(0.0, 100.0))
        _push(rig, zone, 150.0)
        assert [c.code for c in rig.conditions.of(zone)] == [Code.BAND_ALARM]
        _push(rig, zone, invalid("ne43_high", "high"))
        rig.clock.advance(0.5)
        _push(rig, zone, invalid("ne43_high", "high"))
        assert rig.conditions.get(zone, Code.BAND_UNKNOWN) is None, "inside its 1 s grace"
        rig.clock.advance(0.6)
        _push(rig, zone, invalid("ne43_high", "high"))
        unknown = rig.conditions.get(zone, Code.BAND_UNKNOWN)
        assert unknown is not None and unknown.severity is Severity.ERROR
        assert unknown.details == {"quality": "invalid", "reason": "ne43_high", "side": "high"}
        set_rig(rig)
        with TestClient(create_app()) as client:
            alarms = client.get("/api/health").json()["alarms"]
        assert alarms == {"warn": 0, "alarm": 0, "unknown": 1, "max_level": 0}
        for _ in range(2):
            _push(rig, zone, 50.0)
        assert rig.conditions.get(zone, Code.BAND_UNKNOWN) is not None
        _push(rig, zone, 50.0)
        assert rig.conditions.get(zone, Code.BAND_UNKNOWN) is None, "the third ends the episode"

    def test_a_warning_band_ignores_by_default_and_on_no_value_overrides(self, rig, oven):
        zone = self._banded(rig, oven, warning=(0.0, 100.0))
        for _ in range(3):
            _push(rig, zone, invalid())
            rig.clock.advance(1.0)
        assert rig.conditions.of(zone) == []
        zone.set_meta(on_no_value=OnNoValue.FIRE)
        _push(rig, zone, invalid())
        rig.clock.advance(1.0)
        _push(rig, zone, invalid())
        unknown = rig.conditions.get(zone, Code.BAND_UNKNOWN)
        assert unknown is not None and unknown.severity is Severity.WARNING

    def test_benign_and_device_counted_no_values_never_fire(self, rig, oven):
        from flyball.foundation.device import Reason, stale

        zone = self._banded(rig, oven, alarm=(0.0, 100.0))
        for value in (not_applicable(), stale(Reason.DEVICE_OFFLINE)):
            for _ in range(3):
                _push(rig, zone, value)
                rig.clock.advance(2.0)
        assert rig.conditions.of(zone) == []

    def test_a_railed_reading_that_may_lie_past_an_edge_leaves_the_band_as_it_was(self, rig, oven):
        zone = self._banded(rig, oven, alarm=(0.0, 100.0))
        _push(rig, zone, railed(100.0, "high"))
        assert rig.conditions.of(zone) == []


# endregion

# region A6: echo demands while writes fail


class TestWriteFailed:
    def test_every_echo_demand_goes_stale_and_the_sibling_recovers(self, rig, oven):
        heater, fan, valve = (oven.signals[n] for n in ("heater", "fan", "valve"))
        rig.write(oven.root, {"heater": 10.0, "fan": 5.0})
        oven.fail_commits = True
        with pytest.raises(OSError):
            rig.write(oven.root, {"heater": 20.0})
        gone = NoValue(Quality.STALE, "write_failed")
        assert _latest(rig, heater) == gone and _latest(rig, fan) == gone
        assert rig.router.last_usable[fan].value == 5.0
        assert valve not in rig.router.latest, "a sensed demand is not the write's to judge"
        oven.fail_commits = False
        rig.write(oven.root, {"fan": 6.0})
        assert _latest(rig, fan) == 6.0
        assert _latest(rig, heater) == 20.0, "its failed value stayed staged and went out too"

    def test_a_sibling_not_in_the_recovering_commit_gets_its_last_value_back(self, rig, oven):
        heater, fan = oven.signals["heater"], oven.signals["fan"]
        rig.write(oven.root, {"heater": 10.0, "fan": 5.0})
        oven.fail_commits = True
        with pytest.raises(OSError):
            rig.write(oven.root, {"heater": 20.0})
        oven.fail_commits = False
        rig.write(oven.root, {"heater": 30.0})
        assert _latest(rig, heater) == 30.0 and _latest(rig, fan) == 5.0

    def test_a_pending_echo_demand_stays_pending(self, rig, oven):
        oven.fail_commits = True
        with pytest.raises(OSError):
            rig.write(oven.root, {"heater": 20.0})
        assert oven.signals["fan"] not in rig.router.latest

    def test_the_writer_path_does_the_same(self, rig, fresh):
        import time

        oven = Blocking(fresh("blocking"))
        rig.add_device(oven)
        heater, fan = oven.signals["heater"], oven.signals["fan"]

        def until(condition) -> None:
            deadline = time.monotonic() + 2.0
            while not condition() and time.monotonic() < deadline:
                time.sleep(0.005)
            assert condition()

        rig.write(oven.root, {"heater": 10.0, "fan": 5.0})
        until(lambda: rig.router.latest.get(fan) is not None)
        oven.fail_commits = True
        rig.write(oven.root, {"heater": 20.0})
        gone = NoValue(Quality.STALE, "write_failed")
        until(lambda: rig.router.latest[fan].value == gone)
        assert _latest(rig, heater) == gone
        oven.fail_commits = False
        rig.write(oven.root, {"fan": 7.0})
        until(lambda: rig.router.latest[fan].value == 7.0)
        until(lambda: rig.router.latest[heater].value == 20.0)  # kept, and sent with it

    def test_a_rate_clamp_ramps_from_the_last_value(self, rig, fresh):
        from flyball.foundation.time import Rate, TimeUnit

        class Rated(Oven):
            pass

        oven = Rated(fresh("rated"))
        oven.signals["heater"].set_meta(max_rate=Rate(1.0, TimeUnit.SECOND))
        rig.add_device(oven)
        rig.write(oven.root, {"heater": 0.0})
        oven.fail_commits = True
        with pytest.raises(OSError):
            rig.write(oven.root, {"fan": 1.0})
        oven.fail_commits = False
        rig.clock.advance(1.0)
        states = rig.write(oven.root, {"heater": 100.0})
        assert states[oven.signals["heater"]].value == 1.0


# endregion

# region stale(device_offline)


class TestDeviceOffline:
    def test_its_read_path_goes_stale_at_once_and_comes_back_with_a_read(self, rig, clock, oven):
        oven.poll_s = 1.0
        rig.write(oven.root, {"heater": 10.0})
        rig.start_polling(oven)
        clock.advance(1.0)
        zone, valve, heater = (oven.signals[n] for n in ("zone", "valve", "heater"))
        assert _latest(rig, zone) == 20.0
        oven.fail_reads = True
        clock.advance(3.0)
        assert rig.conditions.get(oven, Code.OFFLINE) is not None
        gone = NoValue(Quality.STALE, "device_offline")
        assert _latest(rig, zone) == gone and _latest(rig, valve) == gone
        assert _latest(rig, heater) == 10.0, "an echo demand is what was written"
        oven.fail_reads = False
        clock.advance(2.0)
        assert _latest(rig, zone) == 20.0


# endregion

# region The store (A2)


class TestTheStoreFlag:
    def _record(self, tmp_path, rig: Rig, oven: Oven) -> tuple[SqliteStore, int]:
        store = SqliteStore(tmp_path / "t.db")
        recorder = rig.start_recording(store)
        zone = oven.signals["zone"]
        oven.signals["heater"].spec  # noqa: B018
        for i, value in enumerate((
            20.0,
            invalid(),
            21.0,
            not_applicable(),
            math.nan,
            railed(500.0, "high"),
            22.0,
        )):
            rig.clock.advance(1.0)
            _push(rig, zone, value)
            del i
        oven.signals["heater"].narrow((0.0, 50.0))
        rig.write(oven.root, {"heater": 80.0})
        session = recorder.writer.session.id
        rig.stop_recording()
        return store, session

    def test_no_values_are_null_with_their_code_and_marks_ride_value_rows(
        self, tmp_path, rig, oven
    ):
        store, session = self._record(tmp_path, rig, oven)
        zone = f"{oven.name}.zone"
        points = store.series(session, zone).points
        assert [(p.value, p.flag) for p in points] == [
            (20.0, None),
            (None, Flag.INVALID),
            (21.0, None),
            (None, Flag.NOT_APPLICABLE),
            (None, Flag.INVALID),
            (500.0, Flag.AT_LIMIT_HIGH),
            (22.0, None),
        ]
        heater = store.series(session, f"{oven.name}.heater").points
        assert heater[-1].value == 50.0 and heater[-1].flag == Flag.AT_LIMIT_HIGH
        rows = store.samples(session, oven.name)
        assert any(r.flags.get(zone) == Flag.INVALID and r.values[zone] is None for r in rows)

    def test_thinning_keeps_every_break_and_a_bucket_with_one_is_none(self, tmp_path, rig, oven):
        store, session = self._record(tmp_path, rig, oven)
        zone = f"{oven.name}.zone"
        thinned = store.series(session, zone, downsample=Downsample(every=1000))
        assert [p.value for p in thinned.points] == [None, None, None]
        buckets = store.series(session, zone, downsample=Downsample(bucket_ns=10_000_000_000))
        (bucket,) = buckets.points
        assert bucket.value is None and bucket.flag == Flag.INVALID

    def test_a_store_recorded_before_the_flag_migrates_with_its_readings(
        self, tmp_path, oven, monkeypatch
    ):
        from flyball.record import migrate

        shipped = migrate.available
        monkeypatch.setattr(
            migrate, "available", lambda: {v: p for v, p in shipped().items() if v <= 20}
        )
        store = SqliteStore(tmp_path / "old.db")
        writer = store.open_session(0)
        zone = oven.signals["zone"]
        writer.declare_device(oven)
        writer.declare_signal(zone)
        session = writer.session.id
        store.close()
        old = sqlite3.connect(tmp_path / "old.db")
        (did,) = old.execute("SELECT id FROM device WHERE session_id = ?", (session,)).fetchone()
        (sid,) = old.execute(
            "SELECT id FROM signal WHERE session_id = ? AND address = ?", (session, zone.address)
        ).fetchone()
        for seq, value in ((1, 20.0), (2, 21.0)):
            old.execute(
                "INSERT INTO sample (session_id, device_id, seq, node, offset_ns)"
                " VALUES (?, ?, ?, ?, ?)",
                (session, did, seq, oven.name, seq * 1_000_000_000),
            )
            old.execute(
                "INSERT INTO reading (session_id, device_id, seq, signal_id, offset_ns, value)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (session, did, seq, sid, seq * 1_000_000_000, value),
            )
        old.commit()
        old.close()
        monkeypatch.setattr(migrate, "available", shipped)
        store = SqliteStore(tmp_path / "old.db")
        points = store.series(session, zone.address).points
        assert [(p.value, p.flag) for p in points] == [(20.0, None), (21.0, None)]
        store.close()

    def test_the_checks_refuse_a_mismatched_pair(self, tmp_path):
        store = SqliteStore(tmp_path / "t.db")
        connection = sqlite3.connect(tmp_path / "t.db")
        columns = [r[1] for r in connection.execute("PRAGMA table_info(reading)")]
        assert columns[-2:] == ["value", "flag"]
        index = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'reading_by_signal'"
        ).fetchone()[0]
        assert "value, flag" in index
        connection.execute("PRAGMA foreign_keys = OFF")
        insert = (
            "INSERT INTO reading (session_id, device_id, seq, signal_id, offset_ns, value, flag)"
            " VALUES (1, 1, ?, 1, 0, ?, ?)"
        )
        for seq, (value, flag) in enumerate([
            (None, None),
            (1.0, 3),
            (None, 16),
            (None, 0),
            (1.0, 32),
            (1.0, 2.5),
        ]):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, (seq, value, flag))
        for seq, (value, flag) in enumerate([(1.0, None), (None, 3), (1.0, 16), (1.0, 31)]):
            connection.execute(insert, (100 + seq, value, flag))
        connection.close()
        store.close()


# endregion

# region The wire


class TestTheWire:
    def test_read_answers_null_with_quality_reason_last_usable_and_age(self, rig, oven):
        zone = oven.signals["zone"]
        rig.clock.advance(1.0)
        _push(rig, zone, 21.0)
        rig.clock.advance(2.0)
        _push(rig, zone, invalid("crc"))
        set_rig(rig)
        with TestClient(create_app()) as client:
            read = client.get(f"/api/read/{zone.address}").json()
            device = client.get(f"/api/devices/{oven.name}").json()
        assert read == {
            "reading": {
                "signal": zone.address,
                "time_ns": 3_000_000_000,
                "value": None,
                "quality": "invalid",
                "reason": "crc",
                "last_usable": {"time_ns": 1_000_000_000, "value": 21.0, "quality": "ok"},
                "age_s": 2.0,
            }
        }
        (out,) = [s for s in device["signals"] if s["name"] == "zone"]
        assert out["quality"] == "invalid" and out["latest"]["value"] is None
        assert out["last_usable"]["value"] == 21.0
        (fan,) = [s for s in device["signals"] if s["name"] == "fan"]
        assert fan["quality"] == "pending" and fan["readback"] == "echo"

    def test_the_samples_stream_carries_null_and_sparse_quality_and_caveats(self, rig, oven):
        zone = oven.signals["zone"]
        set_rig(rig)
        with TestClient(create_app()) as client:
            _push(rig, zone, 600.0)
            with client.websocket_connect("/ws/samples") as ws:
                first = ws.receive_json()["samples"][0]
                assert first["caveats"] == {"zone": {"out_of_range": "high"}}
                assert "quality" not in first
                rig.clock.advance(1.0)
                _push(rig, zone, invalid("ne43_low", "low"))
                frame = ws.receive_json()["samples"][0]
        assert frame["values"]["zone"] is None
        assert frame["quality"] == {"zone": "invalid"} and frame["reason"] == {"zone": "ne43_low"}


# endregion


def test_a_signal_spec_readback_and_on_no_value_come_from_the_rig_file():
    from flyball.foundation.device.entry import SignalMeta

    meta = SignalMeta.model_validate({"on_no_value": "ignore"})
    assert meta.on_no_value is OnNoValue.IGNORE
    with pytest.raises(ValueError):
        SignalMeta.model_validate({"on_no_value": "maybe"})
