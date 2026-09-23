"""The rig's side of the device model: resolve, read, demand, bind, and one delivery."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterator
from typing import Annotated

import pytest

from flyball.control.laws import P
from flyball.foundation.device import (
    Access,
    AddressNotFoundError,
    Committable,
    Demand,
    Node,
    NodeSpec,
    Readable,
    Reading,
    Readout,
    Role,
    Sample,
    Severity,
    Signal,
    SignalRef,
    SignalSpec,
    WriteState,
    command,
)
from flyball.foundation.errors import ConflictError, NotReadyError
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius, Percent, Watt
from flyball.foundation.time import Rate, TimeUnit
from flyball.model.feedforward import NoFeedforward
from flyball.model.law import Transfer
from flyball.rig import Rig, SignalClaimedError

TEMP = Quantity("temperature", Celsius)
POWER = Quantity("power", Watt)
HUMIDITY = Quantity("humidity", Percent)
FLOW = Quantity("flow", "L/min")


class Furnace(Readable, Committable):
    """RP zones and W heaters on one flat tree; counts what the rig asks of it."""

    TREE = (
        SignalSpec(name="zone1", quantity=TEMP, access=Access.RP),
        SignalSpec(name="zone2", quantity=TEMP, access=Access.RP),
        SignalSpec(name="sample", quantity=TEMP, access=Access.RP),
        SignalSpec(
            name="heater1",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            limits=(0.0, 2500.0),
        ),
        SignalSpec(
            name="heater2",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            limits=(0.0, 6000.0),
        ),
        SignalSpec(name="setpoint", quantity=TEMP, access=Access.RW),
    )

    def __init__(self, name: str, label: str | None = None) -> None:
        super().__init__(name, label)
        self.temps = {self.signals[n]: 20.0 for n in ("zone1", "zone2", "sample")}
        self.inputs: dict[str, float] = {}
        self.reads = 0
        self.commits = 0
        self.fail = False
        self.due = True

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.reads += 1
        if self.fail:
            raise OSError("modbus timeout")
        if self.due:
            yield Sample(self.root, time_ns, dict(self.temps))

    def commit(self, time_ns: int) -> None:
        self.commits += 1
        super().commit(time_ns)

    def write_signal(self, signal: Signal, value: float) -> None:
        self.inputs[signal.name] = value


def _sensor(name: str) -> NodeSpec:
    return NodeSpec(
        name=name,
        atomic=True,
        children=(
            SignalSpec(name="humidity", quantity=HUMIDITY, access=Access.RP),
            SignalSpec(name="temperature", quantity=TEMP, access=Access.RP),
            SignalSpec(name="heater", quantity=FLOW, access=Access.RW),  # a setting: read on demand
        ),
    )


class Sensors(Readable):
    """Three atomic namespaces, each read in its own transaction."""

    TREE = (_sensor("chamber"), _sensor("dry"), _sensor("wet"))

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.humidity = {"chamber": 45.0, "dry": 4.1, "wet": 95.0}
        self.reads: list[Node | None] = []

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.reads.append(node)
        nodes = self.root.descendants() if node is None or node is self.root else (node,)
        for n in nodes:
            humidity, temperature = n.signals["humidity"], n.signals["temperature"]
            yield Sample(n, time_ns, {humidity: self.humidity[n.name], temperature: 21.9})


class Blender(Readable, Committable):
    """A composite actuator: bound inputs and pending values meet in one `commit`."""

    TREE = (
        SignalSpec(
            name="humidity",
            quantity=HUMIDITY,
            role=Role.DEMAND,
            access=Access.RPW,
            limits=(0.0, 100.0),
        ),
        SignalSpec(name="dry_flow", quantity=FLOW, role=Role.DEMAND, access=Access.RPW),
        SignalSpec(name="wet_flow", quantity=FLOW, role=Role.DEMAND, access=Access.RPW),
        SignalSpec(name="blend_flow", quantity=FLOW, access=Access.RW),
        SignalSpec(name="expected_humidity", quantity=HUMIDITY, access=Access.RP),
    )

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.flow, self.expected = self.signals["blend_flow"], self.signals["expected_humidity"]
        self.supply: dict[str, float | dict[str, float]] = {}
        self.target = 50.0
        self.blend_flow = 1.0
        self.commits = 0
        self.pump_writes: list[tuple[float, float]] = []

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """A fresh read yields the setting beside what publishes; it is not streamed."""
        yield Sample(self.root, time_ns, {self.flow: self.blend_flow, self.expected: 50.0})

    def commit(self, time_ns: int) -> None:
        """Pull every bound input's newest value -- there is no callback any more."""
        self.commits += 1
        for role, target in self.bound.items():
            if isinstance(target, Signal):
                if (reading := target.reading) is not None:
                    self.supply[role] = reading.value
            else:
                sample = self.router.sample(target)
                published = None if sample is None else sample.published()
                if published is not None:
                    self.supply[role] = published.by_name()
        pending = {signal.name: value for signal, value in self.staged.items()}
        self.target = pending.get("humidity", self.target)
        self.blend_flow = pending.get("blend_flow", self.blend_flow)
        dry = self.supply.get("dry", 0.0)
        self.pump_writes.append((dry if isinstance(dry, float) else 0.0, self.target))


class RateLimited(Committable):
    """One demand capped at 10 units/s, one with no cap at all, for the rate clamp."""

    TREE = (
        SignalSpec(
            name="limited",
            quantity=POWER,
            role=Role.DEMAND,
            access=Access.RPW,
            max_rate=Rate(10.0, TimeUnit.SECOND),
        ),
        SignalSpec(name="unlimited", quantity=POWER, role=Role.DEMAND, access=Access.RPW),
    )


class Thermostat(Committable):
    """A demand driven off a source that goes stale after 5s unread."""

    TREE = (
        SignalSpec(name="zone", quantity=TEMP, access=Access.RP, stale_after_s=5.0),
        SignalSpec(name="heater", quantity=POWER, role=Role.DEMAND, access=Access.RPW),
    )


class Supplied(Committable):
    """A demand bounded by a reading that may not have arrived yet: a supply line's humidity."""

    supply = Readout("supply", "Supply humidity", HUMIDITY)
    chamber = Readout("chamber", "Chamber humidity", HUMIDITY)
    humidity = Demand("humidity", "Target humidity", HUMIDITY, limits=(0.0, supply))

    @command
    def aim(self, humidity: Annotated[float, humidity]) -> float:
        """Aim at a humidity: a linked argument, clamped like a demand."""
        return humidity


class Stage(Committable):
    """A W namespace written whole."""

    TREE = (
        NodeSpec(
            name="position",
            atomic=True,
            children=(
                SignalSpec(
                    name="x", quantity=Quantity("x", "mm"), role=Role.DEMAND, access=Access.RPW
                ),
                SignalSpec(
                    name="y", quantity=Quantity("y", "mm"), role=Role.DEMAND, access=Access.RPW
                ),
            ),
        ),
    )


@pytest.fixture
def furnace(rig, fresh) -> Furnace:
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    return furnace


@pytest.fixture
def sensors(rig, fresh) -> Sensors:
    sensors = Sensors(fresh("hum"))
    rig.add_device(sensors)
    return sensors


@pytest.fixture
def blender(rig, fresh) -> Blender:
    blender = Blender(fresh("blender"))
    rig.add_device(blender)
    return blender


class TestNames:
    def test_add_device_claims_the_name_rig_wide(self, rig, furnace):
        rig.add_device(furnace)  # the same object again is fine
        assert rig.devices[furnace.name] is furnace and rig.kind_of(furnace.name) == "device"
        with pytest.raises(ConflictError, match="already used by device"):
            rig.add_device(Furnace(furnace.name))
        with pytest.raises(ConflictError, match="reserved"):
            rig.add_device(Furnace("schema"))

    def test_a_device_shares_the_namespace_with_every_other_claim(self, rig, furnace, fresh):
        """Whatever claimed a name first keeps it, and a failed add claims nothing."""
        name = fresh("sim")
        rig.claim(name, "simulation", object())
        with pytest.raises(ConflictError, match=f"already used by simulation {name!r}"):
            rig.add_device(Furnace(name))
        assert name not in rig.devices
        with pytest.raises(ConflictError, match="already used by device"):
            rig.claim(furnace.name, "simulation", object())
        rig.release(furnace.name)
        assert rig.kind_of(furnace.name) is None and furnace.name not in rig.devices
        rig.add_device(Furnace(furnace.name))


class TestResolve:
    def test_walks_device_namespace_signal(self, rig, sensors):
        assert rig.resolve(sensors.name) is sensors.root
        assert rig.resolve(f"{sensors.name}.dry") is sensors.nodes["dry"]
        assert rig.resolve(f"{sensors.name}.dry.humidity") is sensors.signals["dry.humidity"]

    def test_an_unknown_address_names_the_segment(self, rig, sensors, fresh):
        with pytest.raises(
            AddressNotFoundError, match="'nowhere.x' not found: no device 'nowhere'"
        ):
            rig.resolve("nowhere.x")
        with pytest.raises(AddressNotFoundError, match=f"no 'humidty' under {sensors.name}.dry"):
            rig.resolve(f"{sensors.name}.dry.humidty")
        with pytest.raises(AddressNotFoundError, match=f"no 'x' under {sensors.name}.dry.humidity"):
            rig.resolve(f"{sensors.name}.dry.humidity.x")


class TestDelivery:
    def test_a_sample_with_a_stray_key_is_refused_naming_the_node(self, rig, furnace):
        zone1, heater1 = furnace.signals["zone1"], furnace.signals["heater1"]
        heater1.restrict(Access.W)  # a signal with nothing readable, to trigger the refusal
        with pytest.raises(
            ValueError,
            match=rf"Sample on '{furnace.name}' carries '{furnace.name}.heater1' \[w\],"
            " which is not readable",
        ):
            rig.on_samples([Sample(furnace.root, 0, {zone1: 1.0, heater1: 1.0})])
        with pytest.raises(
            ValueError, match=f"Sample on '{furnace.name}' carries 'pressure', not a bound signal"
        ):
            rig.on_samples([Sample(furnace.root, 0, {"pressure": 1.0})])  # type: ignore[dict-item]
        with pytest.raises(
            ValueError, match=f"Sample on '{furnace.name}' carries 'nowhere', not a bound signal"
        ):
            rig.on_samples([Sample(furnace.root, 0, {"nowhere": 1.0})])  # type: ignore[dict-item]
        assert set(rig.latest) == set(), "refused before any of it was applied"

    def test_a_key_must_be_a_signal_under_the_node(self, rig, sensors, furnace):
        hum, dry = sensors.name, sensors.nodes["dry"]
        dry_h, wet_h = sensors.signals["dry.humidity"], sensors.signals["wet.humidity"]
        with pytest.raises(
            ValueError,
            match=f"Sample on '{hum}' carries '{hum}.dry' is a namespace, not a bound signal",
        ):
            rig.on_samples([Sample(sensors.root, 0, {dry: 1.0, wet_h: 1.0})])  # type: ignore[dict-item]
        with pytest.raises(
            ValueError,
            match=f"Sample on '{hum}.dry' carries '{hum}.wet.humidity', which is not under it",
        ):
            rig.on_samples([Sample(dry, 0, {dry_h: 1.0, wet_h: 1.0})])
        zone1 = furnace.signals["zone1"]
        with pytest.raises(
            ValueError,
            match=f"Sample on '{hum}.dry' carries '{furnace.name}.zone1', which is not under it",
        ):
            rig.on_samples([Sample(dry, 0, {dry_h: 1.0, zone1: 1.0})])
        assert set(rig.latest) == set()

    def test_a_sample_on_the_root_carries_its_subtree(self, rig, sensors):
        dry, wet = sensors.nodes["dry"], sensors.nodes["wet"]
        dry_h, dry_t = sensors.signals["dry.humidity"], sensors.signals["dry.temperature"]
        wet_h = sensors.signals["wet.humidity"]
        whole = Sample(sensors.root, 7, {dry_h: 4.0, wet_h: 96.0})
        with rig.samples.watch():
            rig.on_samples([whole])
        assert rig.latest == {
            dry_h: Reading(dry_h, 7, 4.0),
            wet_h: Reading(wet_h, 7, 96.0),
        }
        assert rig.samples.changed_since(0)[1] == {sensors.name: whole}, "flat, as delivered"
        assert rig.read(rig.resolve(f"{sensors.name}.dry")) == Sample(dry, 7, {dry_h: 4.0})
        assert rig.read(wet) == Sample(wet, 7, {wet_h: 96.0})
        assert rig.read(wet).by_name() == {"humidity": 96.0}, "the wire key is relative"
        assert list(rig.read(sensors.root)) == [whole], "once, not again per namespace"
        # The newest instant on a node wins, whichever node it was delivered on.
        direct = Sample(dry, 8, {dry_h: 4.5, dry_t: 20.0})
        rig.on_samples([direct])
        assert rig.read(dry) is direct
        rig.on_samples([Sample(sensors.root, 9, {dry_t: 21.0})])
        assert rig.read(dry) == Sample(dry, 9, {dry_t: 21.0})

    def test_a_readable_setting_lands_in_latest_but_is_not_streamed(self, rig, blender, clock):
        flow, expected = blender.signals["blend_flow"], blender.signals["expected_humidity"]
        with rig.samples.watch():
            rig.on_samples([Sample(blender.root, 3, {flow: 1.5, expected: 49.0})])
            rig.on_samples([Sample(blender.root, 4, {flow: 1.6})])
        assert rig.latest == {
            flow: Reading(flow, 4, 1.6),
            expected: Reading(expected, 3, 49.0),
        }
        assert rig.samples.changed_since(0)[1] == {
            blender.name: Sample(blender.root, 3, {expected: 49.0})
        }, "cut to what publishes; a sample with nothing left is not set at all"
        assert rig.recent_readings(flow) == [Reading(flow, 3, 1.5), Reading(flow, 4, 1.6)]
        # A fresh read of the setting goes through the same delivery.
        rig.write(blender.root, {"blend_flow": 2.5})
        clock.advance(1.0)
        assert rig.read(flow, fresh=True) == Reading(flow, clock.now_ns(), 2.5)
        assert rig.latest[flow].value == 2.5

    def test_a_sample_with_no_values_is_refused_naming_the_node(self, rig, furnace):
        with pytest.raises(ValueError, match=f"Sample on '{furnace.name}' carries no values"):
            rig.on_samples([Sample(furnace.root, 0, {})])
        rig.on_samples([])  # no samples at all is nothing to do

    def test_a_partial_sample_updates_only_what_it_carries(self, rig, furnace):
        zone1, zone2, sample = (furnace.signals[n] for n in ("zone1", "zone2", "sample"))
        rig.on_samples([Sample(furnace.root, 10, {zone1: 21.0, zone2: 22.0, sample: 23.0})])
        rig.on_samples([Sample(furnace.root, 20, {zone1: 31.0})])
        assert rig.latest[zone1] == Reading(zone1, 20, 31.0)
        assert rig.latest[zone2] == Reading(zone2, 10, 22.0)
        assert rig.recent_readings(zone1) == [
            Reading(zone1, 10, 21.0),
            Reading(zone1, 20, 31.0),
        ]

    def test_on_samples_updates_latest_and_samples(self, rig, sensors):
        dry = sensors.nodes["dry"]
        humidity, temperature = sensors.signals["dry.humidity"], sensors.signals["dry.temperature"]
        first = Sample(dry, 5, {humidity: 4.1, temperature: 21.9})
        rig.on_samples([first])
        assert rig.samples.changed_since(0)[1] == {}, "built only while watched"
        second = Sample(dry, 6, {humidity: 4.2, temperature: 21.9})
        with rig.samples.watch():
            rig.on_samples([second])
        assert rig.samples.changed_since(0)[1] == {f"{sensors.name}.dry": second}
        assert rig.latest[humidity] == Reading(humidity, 6, 4.2)
        assert rig.read(dry) is second


class TestRead:
    def test_a_signal_read_returns_the_last_reading_and_fresh_hits_the_device(
        self, rig, furnace, clock
    ):
        zone1 = furnace.signals["zone1"]
        with pytest.raises(NotReadyError, match=f"Nothing has been read on '{zone1.address}' yet"):
            rig.read(zone1)
        rig.on_samples([Sample(furnace.root, 10, {zone1: 21.0})])
        assert rig.read(zone1) == Reading(zone1, 10, 21.0) and furnace.reads == 0
        furnace.temps[zone1] = 99.0
        clock.advance(1.0)
        assert rig.read(zone1, fresh=True) == Reading(zone1, clock.now_ns(), 99.0)
        assert furnace.reads == 1
        assert rig.latest[zone1].value == 99.0, "delivered, not just returned"
        heater1 = furnace.signals["heater1"]
        heater1.restrict(Access.W)  # a signal with nothing readable
        with pytest.raises(ConflictError, match=rf"'{furnace.name}.heater1' \[w\] is not readable"):
            rig.read(heater1)

    def test_an_atomic_node_reads_as_a_sample_and_a_device_as_samples(self, rig, sensors, clock):
        dry, wet = sensors.nodes["dry"], sensors.nodes["wet"]
        with pytest.raises(NotReadyError, match=f"'{dry.address}'"):
            rig.read(dry)
        sample = rig.read(dry, fresh=True)
        assert isinstance(sample, Sample) and sample.node is dry
        assert sample.by_name() == {"humidity": 4.1, "temperature": 21.9}
        assert sample.values == {dry.signals["humidity"]: 4.1, dry.signals["temperature"]: 21.9}
        assert sensors.reads == [dry], "the bound node reaches the driver"
        assert rig.latest[sensors.signals["dry.humidity"]].value == 4.1
        assert list(rig.read(sensors.root)) == [sample], "not fresh: what is known"
        samples = list(rig.read(sensors.root, fresh=True))
        assert [s.node for s in samples] == [sensors.nodes["chamber"], dry, wet]
        assert rig.read(wet) is samples[2]

    def test_several_targets_read_in_order_with_one_read_per_device(
        self, rig, sensors, furnace, clock
    ):
        dry, wet = sensors.nodes["dry"], sensors.nodes["wet"]
        dry_h, zone1 = sensors.signals["dry.humidity"], furnace.signals["zone1"]
        results = rig.read([zone1, dry, dry_h], fresh=True)
        assert sensors.reads == [dry], "one node asked for on the device: read on it"
        assert furnace.reads == 1
        assert results[0] == Reading(zone1, clock.now_ns(), 20.0)
        assert isinstance(results[1], Sample) and results[1].node is dry
        assert results[2] == Reading(dry_h, clock.now_ns(), 4.1)
        results = rig.read([wet, dry_h], fresh=True)
        assert sensors.reads == [dry, None], "two nodes on one device: one read of the root"
        assert results[0].node is wet and results[1].value == 4.1
        assert rig.read([zone1, wet]) == [rig.latest[zone1], results[0]], "not fresh: known"
        assert rig.read([]) == []


class TestDemand:
    def test_refuses_a_signal_that_is_not_writable(self, rig, furnace):
        with pytest.raises(ConflictError, match=rf"'{furnace.name}.zone1' \[rp\] is not writable"):
            rig.write(furnace.root, {"zone1": 1.0})
        with pytest.raises(AddressNotFoundError, match=f"no 'heater9' under {furnace.name}"):
            rig.write(furnace.root, {"heater9": 1.0})
        with pytest.raises(ValueError, match=f"Demand on '{furnace.name}' carries no values"):
            rig.write(furnace.root, {})
        assert furnace.staged == {} and furnace.commits == 0

    def test_dry_and_wet_flow_are_independent_demands(self, rig, blender):
        """`together` is gone: each flow signal is its own demand now."""
        dry_flow, wet_flow = blender.signals["dry_flow"], blender.signals["wet_flow"]
        states = rig.write(blender.root, {"dry_flow": 0.4})
        assert states == {dry_flow: WriteState(value=0.4)}
        states = rig.write(blender.root, {dry_flow: 0.5, "wet_flow": 0.5})
        assert states == {dry_flow: WriteState(value=0.5), wet_flow: WriteState(value=0.5)}

    def test_signal_keys_and_names_are_the_same_demand(self, rig, furnace):
        heater1, heater2 = furnace.signals["heater1"], furnace.signals["heater2"]
        by_name = rig.write(furnace.root, {"heater1": 3000.0, "heater2": 100.0})
        by_signal = rig.write(furnace.root, {heater1: 3000.0, heater2: 100.0})
        assert by_name == by_signal
        assert by_signal[heater1] == WriteState(value=2500.0, requested=3000.0, at_limit="high")
        assert furnace.commits == 2 and furnace.inputs == {"heater1": 2500.0, "heater2": 100.0}
        with pytest.raises(
            ValueError, match=f"Demand on '{furnace.name}' names '{heater1.address}' twice"
        ):
            rig.write(furnace.root, {"heater1": 1.0, heater1: 2.0})
        assert furnace.commits == 2

    def test_a_signal_key_must_be_under_the_node(self, rig, furnace, fresh):
        stage = Stage(fresh("stage"))
        rig.add_device(stage)
        heater1, x = furnace.signals["heater1"], stage.signals["position.x"]
        with pytest.raises(ConflictError, match=f"'{x.address}' is not under '{furnace.name}'"):
            rig.write(furnace.root, {heater1: 1.0, x: 1.0})
        with pytest.raises(
            ConflictError, match=f"'{heater1.address}' is not under '{stage.name}.position'"
        ):
            rig.write(stage.nodes["position"], {heater1: 1.0})
        assert furnace.staged == {} and stage.staged == {}
        assert rig.write(stage.root, {x: 1.0}) == {x: WriteState(value=1.0)}

    def test_a_manual_demand_commits_now_and_reports_the_clamp(self, rig, furnace):
        heater1, heater2 = furnace.signals["heater1"], furnace.signals["heater2"]
        states = rig.write(furnace.root, {"heater1": 3000.0, "heater2": 100.0})
        assert furnace.commits == 1 and furnace.inputs == {"heater1": 2500.0, "heater2": 100.0}
        assert states[heater1] == WriteState(value=2500.0, requested=3000.0, at_limit="high")
        assert states[heater2] == WriteState(value=100.0)
        assert furnace.written[heater1] == states[heater1], "the wire sees the same"
        with rig.write_states.watch():
            rig.write(furnace.root, {"heater1": -1.0})
        assert rig.write_states.changed_since(0)[1] == {
            f"{furnace.name}.heater1": WriteState(value=0.0, requested=-1.0, at_limit="low")
        }
        assert rig.write(furnace.root, {"heater1": 2500.0})[heater1].requested is None

    def test_a_dotted_name_reaches_a_signal_under_a_namespace(self, rig, fresh):
        stage = Stage(fresh("stage"))
        rig.add_device(stage)
        x, y = stage.signals["position.x"], stage.signals["position.y"]
        assert rig.write(stage.root, {"position.x": 1.0}) == {x: WriteState(value=1.0)}
        assert rig.write(stage.nodes["position"], {"x": 2.0, "y": 3.0}) == {
            x: WriteState(value=2.0),
            y: WriteState(value=3.0),
        }
        with pytest.raises(ConflictError, match=f"'{stage.name}.position' is a namespace"):
            rig.write(stage.root, {"position": 1.0})

    def test_a_controller_owned_signal_refuses_a_manual_demand(self, rig, furnace):
        heater1 = furnace.signals["heater1"]
        controller = rig.attach_controller(heater1, furnace.signals["zone1"], law=P(kp=1.0))
        rig.write(furnace.root, {"heater1": 0.5})  # attached but manual: takes demands
        controller.regulate(50.0)
        with pytest.raises(
            ConflictError,
            match=f"'{heater1.address}' is driven by controller '{controller.name}'",
        ):
            rig.write(furnace.root, {"heater1": 1.0})
        assert rig.write(furnace.root, {"heater2": 1.0}) == {
            furnace.signals["heater2"]: WriteState(value=1.0)
        }
        assert rig.detach_controller(controller.name) is controller
        assert controller.name not in rig.controllers and controller.mode.value == "manual"
        state = rig.write(furnace.root, {"heater1": 1.0})[heater1]
        assert state == WriteState(value=1.0, controller=None)
        controller.regulate(50.0, transfer=Transfer.COLD)
        assert furnace.written[heater1].value == 1.0, "detached: its demands go nowhere"

    def test_a_demand_within_the_rate_limit_passes_through_unchanged(self, rig, fresh, clock):
        dev = RateLimited(fresh("rated"))
        rig.add_device(dev)
        limited = dev.signals["limited"]
        assert rig.write(dev.root, {limited: 5.0}) == {limited: WriteState(value=5.0)}
        clock.advance(1.0)  # 10 units/s allows up to 15.0 now
        assert rig.write(dev.root, {limited: 12.0}) == {limited: WriteState(value=12.0)}

    def test_a_demand_exceeding_the_rate_limit_is_clamped_and_the_clamped_value_is_recorded(
        self, rig, fresh, clock
    ):
        dev = RateLimited(fresh("rated"))
        rig.add_device(dev)
        limited = dev.signals["limited"]
        rig.write(dev.root, {limited: 5.0})
        clock.advance(1.0)  # 10 units/s allows a step of at most 10.0: 5.0 -> 15.0
        states = rig.write(dev.root, {limited: 100.0})
        assert states == {limited: WriteState(value=15.0, requested=100.0)}
        assert dev.written[limited].value == 15.0, "the clamped value committed, not the ask"

    def test_no_max_rate_configured_behaves_exactly_as_before(self, rig, fresh, clock):
        """No `max_rate` on a signal: an arbitrarily large, instant jump is never clamped."""
        dev = RateLimited(fresh("rated"))
        rig.add_device(dev)
        unlimited = dev.signals["unlimited"]
        rig.write(dev.root, {unlimited: 0.0})
        clock.advance(0.001)
        states = rig.write(dev.root, {unlimited: 10_000.0})
        assert states == {unlimited: WriteState(value=10_000.0)}

    def test_a_controller_demand_is_held_once_its_source_goes_stale(self, rig, fresh, clock):
        thermo = Thermostat(fresh("thermo"))
        rig.add_device(thermo)
        zone, heater = thermo.signals["zone"], thermo.signals["heater"]
        controller = rig.attach_controller(heater, zone, law=P(kp=1.0))
        rig.on_samples([Sample(thermo.root, clock.now_ns(), {zone: 20.0})])
        clock.advance(4.9)  # under the 5s threshold: still trusted
        assert rig.write(thermo.root, {heater: 10.0}, by=controller) == {
            heater: WriteState(value=10.0, controller=controller.name)
        }
        clock.advance(0.2)  # 5.1s since the reading: now stale
        assert rig.write(thermo.root, {heater: 20.0}, by=controller) == {}
        assert thermo.written[heater].value == 10.0, "held: the last applied value stands"


class TestOneControllerFailing:
    """A controller whose step raises is an event, not the end of everyone else's delivery."""

    def test_the_others_still_commit_and_the_failure_is_one_event(self, rig, furnace, clock):
        zone1, zone2 = furnace.signals["zone1"], furnace.signals["zone2"]
        good = rig.attach_controller(furnace.signals["heater1"], zone1, law=P(kp=10.0))
        bad = rig.attach_controller(furnace.signals["heater2"], zone2)  # no law
        good.regulate(30.0)
        bad.regulate(30.0)

        for _ in range(3):
            clock.advance(1.0)
            rig.on_samples([Sample(furnace.root, clock.now_ns(), {zone1: 20.0, zone2: 20.0})])
        assert good.expected == pytest.approx(100.0), "10 * (30 - 20), committed each time"
        assert furnace.commits >= 3
        failed = [e for e in rig.recent if e.code == "step_failed"]
        assert len(failed) == 1 and failed[0].subject == bad.name, "one event per outage"
        assert bad.mode.value == "regulating", "its mode is left alone"
        assert rig.latest[zone1].value == 20.0, "the delivery's readings landed"

        bad.set_law(P(kp=1.0))
        clock.advance(1.0)
        rig.on_samples([Sample(furnace.root, clock.now_ns(), {zone1: 20.0, zone2: 20.0})])
        last = rig.recent[-1]
        assert (last.code, last.edge, last.subject) == ("step_failed", "cleared", bad.name)


class TestLimitsThatFollowASignal:
    """A limit bound to a signal with no value yet fails closed: never an unclamped demand."""

    @pytest.fixture
    def supplied(self, rig, fresh) -> Supplied:
        device = Supplied(fresh("supplied"))
        rig.add_device(device)
        return device

    def test_a_demand_before_the_bound_is_known_is_refused_not_passed_unclamped(
        self, rig, supplied
    ):
        humidity = supplied.signals["humidity"]
        assert humidity.limits is None, "nothing read on the supply yet"
        with pytest.raises(NotReadyError, match=r"limit follows 'supply', which has no value yet"):
            rig.write(supplied.root, {humidity: 150.0})
        assert supplied.written == {} and supplied.staged == {}, "nothing reached the device"
        assert humidity.reading is None

    def test_the_first_reading_of_the_bound_lifts_the_refusal_and_clamps_to_it(
        self, rig, supplied, clock
    ):
        humidity, supply = supplied.signals["humidity"], supplied.signals["supply"]
        with pytest.raises(NotReadyError):
            rig.write(supplied.root, {humidity: 150.0})
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {supply: 95.0})])
        assert rig.write(supplied.root, {humidity: 150.0}) == {
            humidity: WriteState(value=95.0, requested=150.0, at_limit="high")
        }

    def test_a_linked_command_argument_is_refused_while_its_limit_is_unknown(
        self, rig, supplied, clock
    ):
        with pytest.raises(NotReadyError, match="limit"):
            rig.run_command(supplied, "aim", {"humidity": 150.0})
        assert supplied.signals["humidity"].reading is None
        supply = supplied.signals["supply"]
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {supply: 95.0})])
        assert rig.run_command(supplied, "aim", {"humidity": 150.0}) == 95.0

    def test_a_controller_write_is_held_with_one_event_until_the_bound_is_known(
        self, rig, supplied, clock
    ):
        humidity, supply = supplied.signals["humidity"], supplied.signals["supply"]
        chamber = supplied.signals["chamber"]
        controller = rig.attach_controller(humidity, chamber, law=P(kp=1.0))
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {chamber: 40.0})])
        controller.regulate(150.0, transfer=Transfer.COLD)  # the write goes through the rig
        assert controller.expected is None, "held: nothing was applied"
        assert supplied.written == {} and humidity.reading is None
        clock.advance(1.0)
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {chamber: 40.0})])  # a step
        assert supplied.written == {}, "still held on the next step"
        held = [e for e in rig.recent if e.code == "limit_unknown"]
        assert len(held) == 1, "one event on entering the hold, not one per step"
        assert held[0].severity is Severity.WARNING and held[0].subject == controller.name

        rig.on_samples([Sample(supplied.root, clock.now_ns(), {supply: 95.0})])
        clock.advance(1.0)
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {chamber: 40.0})])
        assert supplied.written[humidity].value == 95.0, "applied, clamped to the supply"
        assert supplied.written[humidity].requested is not None
        assert [(e.code, e.edge) for e in rig.recent if e.code.startswith("limit_")] == [
            ("limit_unknown", "raised"),
            ("limit_unknown", "cleared"),
        ]

    def test_a_nan_bound_is_not_known_a_demand_is_refused_not_clamped_to_the_other_end(
        self, rig, supplied, clock
    ):
        humidity, supply = supplied.signals["humidity"], supplied.signals["supply"]
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {supply: math.nan})])
        assert humidity.limits is None, "a NaN bound is no bound to display"
        with pytest.raises(
            NotReadyError, match=r"'supply', which has no value yet, or not a finite"
        ):
            rig.write(supplied.root, {humidity: 150.0})
        assert supplied.written == {} and supplied.staged == {}, "nothing reached the device"
        with pytest.raises(NotReadyError, match="limit"):
            rig.run_command(supplied, "aim", {"humidity": 150.0})
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {supply: 95.0})])
        assert rig.write(supplied.root, {humidity: 150.0})[humidity].value == 95.0

    def test_a_controller_is_held_while_its_bound_is_nan(self, rig, supplied, clock):
        humidity, supply = supplied.signals["humidity"], supplied.signals["supply"]
        chamber = supplied.signals["chamber"]
        controller = rig.attach_controller(humidity, chamber, law=P(kp=1.0))
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {supply: math.inf, chamber: 40.0})])
        controller.regulate(150.0, transfer=Transfer.COLD)
        assert supplied.written == {} and controller.expected is None, "held, not clamped"
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {supply: 95.0})])
        clock.advance(1.0)
        rig.on_samples([Sample(supplied.root, clock.now_ns(), {chamber: 40.0})])
        assert supplied.written[humidity].value == 95.0
        assert [(e.code, e.edge) for e in rig.recent if e.code.startswith("limit_")] == [
            ("limit_unknown", "raised"),
            ("limit_unknown", "cleared"),
        ]

    def test_a_limit_resolves_once_to_the_signal_it_follows(self, supplied):
        humidity, supply = supplied.signals["humidity"], supplied.signals["supply"]
        bounds = humidity.bind_limits()
        assert bounds == (0.0, supply) and bounds[1] is supply, "the object, not its path"
        assert humidity.bind_limits() is bounds, "not resolved again on the next read"

    def test_an_override_of_the_limits_resolves_them_again(self, supplied):
        humidity = supplied.signals["humidity"]
        humidity.set_meta(limits=(0.0, 80.0))
        assert humidity.bind_limits() == (0.0, 80.0)
        assert humidity.limits == (0.0, 80.0), "a number now, known without a supply reading"

    def test_a_limit_that_follows_nothing_fails_when_the_device_is_added(self, rig, fresh):
        device = Supplied(fresh("typo"))
        humidity = device.signals["humidity"]
        humidity.set_meta(limits=(0.0, SignalRef("suply")))
        with pytest.raises(ValueError, match=r"follows 'suply', which is neither a signal nor"):
            rig.add_device(device)
        assert device.name not in rig.devices, "refused before the name is claimed"


class TestControllers:
    def test_attach_controller_wires_the_write_and_refuses_double_claims(self, rig, furnace):
        heater1, heater2 = furnace.signals["heater1"], furnace.signals["heater2"]
        zone1, zone2 = furnace.signals["zone1"], furnace.signals["zone2"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=100.0), feedforward="none")
        assert rig.controllers[heater1.address] is controller
        assert rig.controllers.default == controller.name
        assert isinstance(controller.feedforward, NoFeedforward)
        with pytest.raises(SignalClaimedError, match=f"{heater1.address} is already driven by"):
            rig.attach_controller(heater1, zone2)
        with pytest.raises(SignalClaimedError, match=f"{zone1.address} is already regulated by"):
            rig.attach_controller(heater2, zone1)

        # From outside a delivery -- a program, a route -- the write commits at once.
        rig.on_samples([Sample(furnace.root, 0, {zone1: 40.0})])
        assert furnace.commits == 0, "manual: the tick wrote nothing"
        controller.regulate(50.0, transfer=Transfer.COLD)
        assert furnace.commits == 1 and furnace.inputs == {"heater1": 0.0}
        assert controller.expected == 0.0, "the write returned the committed value"
        assert furnace.written[heater1] == WriteState(
            value=0.0, at_limit="low", controller=controller.name
        )

    def test_two_controllers_on_one_device_coalesce_into_one_commit(self, rig, furnace):
        heater1, heater2 = furnace.signals["heater1"], furnace.signals["heater2"]
        zone1, zone2 = furnace.signals["zone1"], furnace.signals["zone2"]
        c1 = rig.attach_controller(heater1, zone1, law=P(kp=10.0))
        c2 = rig.attach_controller(heater2, zone2, law=P(kp=10.0))
        rig.on_samples([Sample(furnace.root, 0, {zone1: 40.0, zone2: 40.0})])
        c1.regulate(50.0, transfer=Transfer.COLD)
        c2.regulate(60.0, transfer=Transfer.COLD)
        assert furnace.commits == 2, "two handovers outside a delivery: one commit each"
        rig.on_samples([Sample(furnace.root, 1_000_000_000, {zone1: 40.0, zone2: 40.0})])
        assert furnace.commits == 3, "one delivery, two controllers, one commit"
        assert furnace.inputs == {"heater1": 100.0, "heater2": 200.0}
        assert c1.expected == 100.0 and c2.expected == 200.0, "delivered() closed each tick"
        assert c1.delivered_correction == 100.0
        assert furnace.written[heater1].controller == c1.name
        assert furnace.written[heater2].controller == c2.name

    def test_the_controller_gets_delivered_with_the_committed_state(self, rig, furnace):
        heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
        controller = rig.attach_controller(heater1, zone1, law=P(kp=1000.0))
        rig.on_samples([Sample(furnace.root, 0, {zone1: 40.0})])
        controller.regulate(50.0, transfer=Transfer.COLD)
        with rig.controller_states.watch(), rig.write_states.watch():
            rig.on_samples([Sample(furnace.root, 1_000_000_000, {zone1: 40.0})])
        assert controller.output == 10_000.0 and controller.expected == 2500.0, "clamped"
        assert rig.write_states.changed_since(0)[1] == {
            heater1.address: WriteState(
                value=2500.0, requested=10_000.0, at_limit="high", controller=controller.name
            )
        }
        state = rig.controller_states.changed_since(0)[1][controller.name]
        assert state.expected == 2500.0 and state.measured is not None
        assert state.measured.value == 40.0


class TestBoundInputs:
    def test_a_bound_input_s_newest_value_feeds_one_commit(self, rig, sensors, blender):
        dry, chamber = sensors.nodes["dry"], sensors.nodes["chamber"]
        dry_h, dry_t = sensors.signals["dry.humidity"], sensors.signals["dry.temperature"]
        chamber_h, chamber_t = chamber.signals["humidity"], chamber.signals["temperature"]
        rig.bind_inputs(blender, {"dry": f"{sensors.name}.dry.humidity"})
        assert blender.bound == {"dry": dry_h}
        rig.on_samples([Sample(dry, 5, {dry_h: 3.0, dry_t: 20.0})])
        assert blender.supply == {"dry": 3.0} and blender.commits == 1
        assert blender.pump_writes == [(3.0, 50.0)]

        # A bound input and a controller's demand in one delivery: still one commit.
        controller = rig.attach_controller(
            blender.signals["humidity"], sensors.signals["chamber.humidity"], law=P(kp=1.0)
        )
        controller.regulate(47.0, transfer=Transfer.COLD)
        assert blender.commits == 2 and blender.target == 47.0
        rig.on_samples([
            Sample(dry, 6, {dry_h: 4.0}),
            Sample(chamber, 6, {chamber_h: 45.0, chamber_t: 20.0}),
        ])
        assert blender.commits == 3 and blender.supply == {"dry": 4.0}
        assert blender.pump_writes[-1] == (4.0, 49.0)
        assert controller.expected == 49.0

    def test_a_device_bound_to_a_node_only_commits_when_something_under_it_publishes(
        self, rig, sensors, blender
    ):
        dry, chamber = sensors.nodes["dry"], sensors.nodes["chamber"]
        dry_h, dry_t = sensors.signals["dry.humidity"], sensors.signals["dry.temperature"]
        wet_h = sensors.signals["wet.humidity"]
        rig.bind_inputs(blender, {"dry": f"{sensors.name}.dry", "wet": wet_h.address})
        assert blender.bound == {"dry": dry, "wet": wet_h}
        rig.on_samples([Sample(dry, 5, {dry_h: 3.0, dry_t: 20.0})])
        assert blender.commits == 1, "the sample itself when it is the node's"
        assert blender.supply["dry"] == {"humidity": 3.0, "temperature": 20.0}
        whole = Sample(sensors.root, 6, {wet_h: 97.0, dry_h: 3.5})
        rig.on_samples([whole])
        assert blender.commits == 2, "one commit for both the signal and the node binding"
        assert blender.supply == {"dry": {"humidity": 3.5}, "wet": 97.0}, (
            "cut to the bound node, the same keys, after the readings"
        )
        rig.on_samples([Sample(chamber, 7, {chamber.signals["humidity"]: 45.0})])
        assert blender.commits == 2, "nothing under the bound node: not called"
        setting = sensors.signals["dry.heater"]
        rig.on_samples([Sample(dry, 8, {setting: 1.0, dry_h: 4.0})])
        assert blender.commits == 3, "a subscriber hears what publishes"
        assert blender.supply["dry"] == {"humidity": 4.0}, (
            "a fresh read of an R-only setting is not for it"
        )
        rig.on_samples([Sample(dry, 9, {setting: 2.0})])
        assert blender.commits == 3, "nothing published under the bound node: not called"

    def test_bind_inputs_refuses_what_cannot_be_followed(self, rig, sensors, blender, fresh):
        with pytest.raises(AddressNotFoundError, match=f"no 'humidty' under {sensors.name}.dry"):
            rig.bind_inputs(blender, {"dry": f"{sensors.name}.dry.humidty"})
        with pytest.raises(ConflictError, match=r"\[rw\] is not published"):
            rig.bind_inputs(blender, {"dry": f"{blender.name}.blend_flow"})
        stage = Stage(fresh("stage"))
        stage.signals["position.x"].restrict(Access.W)
        stage.signals["position.y"].restrict(Access.W)
        rig.add_device(stage)
        with pytest.raises(
            ConflictError,
            match=f"{blender.name}.inputs.dry: nothing under '{stage.name}.position' publishes",
        ):
            rig.bind_inputs(blender, {"dry": f"{stage.name}.position"})
        assert blender.bound == {}


def test_a_trailing_or_doubled_dot_resolves_to_nothing(rig, sensors):
    for address in (f"{sensors.name}.", f"{sensors.name}.dry.", f"{sensors.name}..dry"):
        with pytest.raises(AddressNotFoundError, match="not found"):
            rig.resolve(address)
    assert rig.resolve(sensors.name) is sensors.root


def test_a_device_may_declare_its_root_atomic(rig, fresh):
    class OneTransaction(Sensors):
        atomic = True

    device = OneTransaction(fresh("sht"))
    rig.add_device(device)
    assert device.root.atomic and not Sensors(fresh("set")).root.atomic
    got = rig.read(device.root, fresh=True)
    assert isinstance(got, Sample), "an atomic root reads as one sample, like an atomic namespace"


class Stuck(Sensors):
    """A read that sits on the bus until released, as a slow transaction would."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.inside = threading.Event()
        self.release = threading.Event()

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        self.inside.set()
        self.release.wait(2.0)
        return super().read(time_ns, node)


class Gated(Committable):
    """A blocking device with one demand and no readback; its commit waits on a gate."""

    blocking = True
    TREE = (SignalSpec(name="heater", quantity=POWER, role=Role.DEMAND, access=Access.RPW),)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.gate = threading.Event()
        self.committed: list[float] = []

    def commit(self, time_ns: int) -> None:
        self.gate.wait(2.0)
        self.committed.extend(self.staged.values())


def _within(seconds: float, fn: Callable[[], object]) -> bool:
    """Run `fn` on a thread; whether it returned within `seconds` of wall time."""
    done = threading.Event()

    def run() -> None:
        fn()
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return done.wait(seconds)


class TestRemoval:
    """Removing a device never waits, under the rig lock, on a thread that needs the lock."""

    def test_removing_a_device_mid_read_returns_at_once(self, fresh):
        rig = Rig()  # the wall clock: the poller is a real thread
        stuck = Stuck(fresh("stuck"))
        rig.add_device(stuck)
        rig.polling.start(stuck, 0.05)
        loop = rig.polling.periodic[stuck.name]
        assert stuck.inside.wait(1.0), "the poller is inside read()"
        removed = _within(1.0, lambda: rig.remove_device(stuck.name))
        stuck.release.set()
        assert removed, "remove_device returned within 1 s"
        assert loop._thread is not None
        loop._thread.join(1.0)
        assert not loop.running, "the poll loop exited after its read"
        assert stuck.name not in rig.polling.by_name and rig.polling.runs.get(stuck.name) is None
        assert not any(s.device is stuck for s in rig.router.latest), "the late read was dropped"

    def test_a_write_completing_after_removal_is_dropped(self, rig, fresh):
        gated = Gated(fresh("gated"))
        rig.add_device(gated)
        heater = gated.signals["heater"]
        rig.write(gated.root, {heater: 1.0})
        writer = rig._writers[gated]
        assert _within(0.5, lambda: rig.remove_device(gated.name)), "no wait on the writer"
        gated.gate.set()
        writer._thread.join(1.0)
        assert not writer._thread.is_alive(), "the writer exited after its commit"
        assert gated.committed == [1.0], "the write in flight reached the bus"
        assert heater not in gated.written and heater not in rig.router.latest


def test_a_blocking_write_with_no_readback_reports_each_committed_value(rig, fresh):
    """The writer snapshots the router before each commit: the second write is not the first's."""
    gated = Gated(fresh("gated"))
    gated.gate.set()
    rig.add_device(gated)
    heater = gated.signals["heater"]
    for value in (1.0, 3.0):
        rig.write(gated.root, {heater: value})
        deadline = time.monotonic() + 2.0
        while gated.written.get(heater) != WriteState(value=value):
            assert time.monotonic() < deadline, f"{value} was reported as {gated.written[heater]}"
            time.sleep(0.005)
        assert rig.router.value(heater) == value
    assert gated.committed == [1.0, 3.0]
    rig.close()


class TestNonFinite:
    """NaN and infinity never reach a device: a demand refuses them, the rate clamp survives one."""

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_a_demand_refuses_a_value_that_is_not_finite(self, rig, furnace, bad):
        rig.write(furnace.root, {"heater1": 10.0})
        with pytest.raises(ValueError, match=rf"'{furnace.name}.heater1' .* not finite"):
            rig.write(furnace.root, {"heater2": 5.0, "heater1": bad})
        assert furnace.inputs == {"heater1": 10.0}, "nothing of the demand was applied"
        assert furnace.commits == 1 and furnace.staged == {}

    def test_a_non_finite_last_value_is_no_rate_reference(self, rig, fresh, clock):
        dev = RateLimited(fresh("rated"))
        rig.add_device(dev)
        limited = dev.signals["limited"]
        limited.push(math.nan, clock.now_ns())  # a driver's readback gone wrong
        assert rig.write(dev.root, {limited: 5.0}) == {limited: WriteState(value=5.0)}
        clock.advance(1.0)
        states = rig.write(dev.root, {limited: 100.0})
        assert states == {limited: WriteState(value=15.0, requested=100.0)}, "5.0 is the reference"

    def test_a_non_finite_demand_over_http_is_a_422(self, rig, furnace):
        from conftest import TestClient
        from flyball.interfaces.server import create_app, set_rig

        set_rig(rig)
        try:
            with TestClient(create_app()) as client:
                put = client.put(
                    f"/api/signals/{furnace.name}.heater1",
                    content="NaN",
                    headers={"content-type": "application/json"},
                )
                assert put.status_code == 422, put.text
                put = client.put(
                    f"/api/devices/{furnace.name}/write",
                    content='{"heater1": Infinity}',
                    headers={"content-type": "application/json"},
                )
                assert put.status_code == 422, put.text
                post = client.post(
                    f"/api/devices/{furnace.name}/commands/set_heater1",
                    content='{"value": NaN}',
                    headers={"content-type": "application/json"},
                )
                assert post.status_code == 422, post.text
        finally:
            set_rig(None)
        assert furnace.commits == 0
