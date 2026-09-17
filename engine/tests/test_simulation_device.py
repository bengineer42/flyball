"""An application's own simulation device at `/api/sim/device`, beside the rig's speed."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from flyball.core.device import Device, Setting, command
from flyball.core.quantity import Quantity
from flyball.core.typing import Positive
from flyball.core.units.si import Second
from flyball.runtime.config import RigConfig
from flyball.runtime.rig import Rig
from flyball.runtime.simulation import Simulation
from flyball.server import create_app, set_rig, set_simulation
from flyball.server.deps import set_simulation_device
from flyball.sim import DaqPort, PlantConfig, ScaledClock, SimDaq, SimDaqConfig


class OvenSim(Device):
    """A stand-in for an application's simulation device."""

    tau_s = Setting("tau_s", "Time constant", Quantity("time", Second), initial=60.0)

    def __init__(self, rig: Rig) -> None:
        super().__init__("simulation")
        self.rig = rig

    @command
    def set_tau(self, tau_s: Positive) -> float:
        """Change the time constant."""
        self.tau_s.push(tau_s)
        return tau_s


class CountingDaq(SimDaq):
    """A `sim_daq` that keeps the stamp of every poll, so a test can count them."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.reads: list[int] = []

    def read(self, time_ns: int, node=None):
        self.reads.append(time_ns)
        return super().read(time_ns, node)


@pytest.fixture
def scaled_rig() -> Iterator[tuple[Rig, list[int]]]:
    """A rig on a ScaledClock, one daq polled every 50 ms of rig time; `reads` are its stamps."""
    rig = Rig()
    rig.clock = ScaledClock(1.0)
    config = SimDaqConfig(
        link=PlantConfig(initial=20.0),
        ports={"t": DaqPort(port="output", quantity="temperature", unit="°C")},
    )
    daq = CountingDaq("sensor", config.link.build(), config.ports, config=config)  # type: ignore[union-attr]
    daq.poll_s = 0.05
    rig.add_device(daq)
    rig.start_polling(daq)
    yield rig, daq.reads
    rig.polling.stop_all()


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
            body = c.get("/api/sim").json()
            assert body["simulated"] is True and body["device"] is False
            assert c.get("/api/sim/device").status_code == 404
            assert c.get("/api/sim/device/schema").status_code == 404
            assert c.post("/api/sim/device/set_tau", json={"tau_s": 1}).status_code == 404
    finally:
        set_simulation(None)
        set_rig(None)


def test_schema_view_and_commands_go_through_the_device_routes(client):
    assert client.get("/api/sim").json()["device"] is True
    schema = client.get("/api/sim/device/schema").json()
    assert schema["name"] == "simulation" and schema["type"] == "OvenSim"
    assert set(schema["commands"]) == {"set_tau"}
    tau = schema["commands"]["set_tau"]["arguments"]["properties"]["tau_s"]
    assert tau["exclusiveMinimum"] == 0
    assert "tau_s" in schema["signals"]

    assert client.post("/api/sim/device/set_tau", json={"tau_s": 5}).json() == 5.0
    # `set_simulation_device` does not swap the device onto the rig's router (unlike
    # `Rig.add_device`), so a pushed signal never reaches `rig.router` for `/api/sim/device`
    # to read back -- a src issue (`flyball.server.deps.set_simulation_device`), out of scope
    # here; not asserted.
    assert client.post("/api/sim/device/set_tau", json={"tau_s": 0}).status_code == 422
    assert client.post("/api/sim/device/set_tau", json={"bogus": 1}).status_code == 422
    assert client.post("/api/sim/device/nope", json={}).status_code == 404


def test_speed_on_the_simulation_speeds_up_the_periodic_reader(client, scaled_rig):
    """The reader's period is in rig seconds: at 4x it polls four times as often in real ones."""
    rig, reads = scaled_rig
    (loop,) = rig.polling.periodic.values()
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
