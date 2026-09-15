"""An application's own simulation device at `/api/sim/device`, beside the rig's speed."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from flyball.core.device import Device, DeviceSettings, DeviceState, command
from flyball.core.reading import Measurand, Source
from flyball.core.typing import Positive
from flyball.core.units.si import Celsius
from flyball.runtime.config import RigConfig
from flyball.runtime.rig import Rig
from flyball.runtime.simulation import Simulation
from flyball.server import create_app, set_rig, set_simulation
from flyball.server.deps import set_simulation_device
from flyball.sim import FunctionReader, ScaledClock


@dataclass(frozen=True, slots=True, kw_only=True)
class OvenSimSettings(DeviceSettings):
    tau_s: Positive = 60.0


@dataclass(frozen=True, slots=True, kw_only=True)
class OvenSimState(DeviceState):
    sim_time_ns: int


class OvenSim(Device):
    """A stand-in for an application's simulation device."""

    def __init__(self, rig: Rig) -> None:
        super().__init__("simulation")
        self.rig = rig
        self.tau_s: float = 60.0

    @property
    def settings(self) -> OvenSimSettings:
        return OvenSimSettings(tau_s=self.tau_s)

    @property
    def state(self) -> OvenSimState:
        return OvenSimState(sim_time_ns=self.rig.clock.now_ns())

    @command
    def set_tau(self, tau_s: Positive) -> OvenSimSettings:
        """Change the time constant."""
        self.tau_s = tau_s
        return self.settings


@pytest.fixture
def scaled_rig(fresh) -> Iterator[tuple[Rig, list[int]]]:
    """A rig on a ScaledClock, one reader polled every 50 ms of rig time; `reads` are its stamps."""
    rig = Rig()
    rig.clock = ScaledClock(1.0)
    reads: list[int] = []
    measurand = Measurand(fresh("t"), Celsius)
    source = Source(fresh("sensor"), (measurand,))

    def model(time_ns: int):
        reads.append(time_ns)
        return {measurand: 20.0}

    rig.start_reader(FunctionReader(fresh("reader"), {source: model}), 0.05)
    yield rig, reads
    rig.readers.stop_all()


@pytest.fixture
def client(scaled_rig) -> Iterator[TestClient]:
    rig, _ = scaled_rig
    set_rig(rig)
    set_simulation(Simulation(rig, RigConfig(name="test")))  # no file, no plants: just the clock
    set_simulation_device(OvenSim(rig))
    with TestClient(create_app()) as c:
        yield c
    set_simulation_device(None)
    set_simulation(None)
    set_rig(None)


def test_without_a_device_the_route_is_404_but_the_simulation_answers(scaled_rig):
    rig, _ = scaled_rig
    set_rig(rig)
    set_simulation(Simulation(rig, RigConfig(name="test")))
    try:
        with TestClient(create_app()) as c:
            assert c.get("/api/sim").json()["simulated"] is True
            assert c.get("/api/sim/device").status_code == 404
            assert c.get("/api/sim/device/schema").status_code == 404
            assert c.post("/api/sim/device/set_tau", json={"tau_s": 1}).status_code == 404
    finally:
        set_simulation(None)
        set_rig(None)


def test_schema_view_and_commands_go_through_the_device_routes(client):
    schema = client.get("/api/sim/device/schema").json()
    assert schema["name"] == "simulation" and schema["type"] == "OvenSim"
    assert set(schema["commands"]) == {"set_tau"}
    tau = schema["commands"]["set_tau"]["arguments"]["properties"]["tau_s"]
    assert tau["exclusiveMinimum"] == 0
    assert "sim_time_ns" in schema["state"]["properties"]

    view = client.get("/api/sim/device").json()
    assert view["settings"] == {"tau_s": 60.0} and view["state"]["sim_time_ns"] > 0

    assert client.post("/api/sim/device/set_tau", json={"tau_s": 5}).json() == {"tau_s": 5.0}
    assert client.get("/api/sim/device").json()["settings"]["tau_s"] == 5.0
    assert client.post("/api/sim/device/set_tau", json={"tau_s": 0}).status_code == 422
    assert client.post("/api/sim/device/set_tau", json={"bogus": 1}).status_code == 422
    assert client.post("/api/sim/device/nope", json={}).status_code == 404


def test_speed_on_the_simulation_speeds_up_the_periodic_reader(client, scaled_rig):
    """The reader's period is in rig seconds: at 4x it polls four times as often in real ones."""
    rig, reads = scaled_rig
    (loop,) = rig.readers.periodic.values()
    base_period = loop.loop_time
    time.sleep(0.5)
    at_one = len(reads)
    assert client.put("/api/sim/clock", json={"speed": 4}).json() == {"speed": 4.0}
    assert client.get("/api/sim").json()["clock"]["speed"] == 4.0
    assert loop.loop_time == base_period, "the period is in rig time; the clock does the scaling"
    reads.clear()
    time.sleep(0.5)
    at_four = len(reads)
    assert at_four > 2 * at_one, f"{at_four} reads at 4x against {at_one} at 1x"
    assert client.get("/api/sim/device").json()["state"]["sim_time_ns"] <= rig.clock.now_ns()
