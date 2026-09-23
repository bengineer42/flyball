"""Routes that list the rig iterate a snapshot: a device or controller added meanwhile is no error.

A route answers on a server thread (or the event loop) while a delivery,
a composition route or the runner changes the rig's dicts. Each test grows
the rig from inside the route's own loop, the first time it builds an
item, which is what a second thread doing so at that moment looks like.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from conftest import TestClient
from flyball.control.laws import P
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.routes import devices as devices_routes
from flyball.interfaces.server.routes import schema as schema_routes
from flyball.interfaces.server.schemas import ControllerOut
from test_rig_devices import Furnace


@pytest.fixture
def furnace(rig, fresh) -> Furnace:
    furnace = Furnace(fresh("furnace"))
    rig.add_device(furnace)
    return furnace


@pytest.fixture
def client(rig, furnace) -> Iterator[TestClient]:
    set_rig(rig)
    with TestClient(create_app()) as c:
        yield c
    set_rig(None)


def _first_call[F: Callable[..., Any]](fn: F, grow: Callable[[], object]) -> F:
    """`fn`, which calls `grow` once, the first time, before itself."""
    pending = [grow]

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if pending:
            # As another request would: on a thread of its own, not the route's (maybe the
            # event loop, which must not take the rig's lock).
            thread = threading.Thread(target=pending.pop())
            thread.start()
            thread.join()
        return fn(*args, **kwargs)

    return wrapped  # type: ignore[return-value]


def test_read_devices_lists_a_snapshot(client, rig, fresh, monkeypatch):
    added = fresh("added")
    grow = lambda: rig.add_device(Furnace(added))  # noqa: E731
    monkeypatch.setattr(devices_routes, "device_out", _first_call(devices_routes.device_out, grow))
    response = client.get("/api/devices")
    assert response.status_code == 200, response.text
    assert added in rig.devices and added not in [d["name"] for d in response.json()]


def test_read_schema_lists_a_snapshot(client, rig, fresh, monkeypatch):
    added = fresh("added")
    grow = lambda: rig.add_device(Furnace(added))  # noqa: E731
    monkeypatch.setattr(
        schema_routes, "device_schema", _first_call(schema_routes.device_schema, grow)
    )
    response = client.get("/api/schema")
    assert response.status_code == 200, response.text
    assert added in rig.devices and added not in response.json()["devices"]


def test_read_controllers_lists_a_snapshot(client, rig, furnace, monkeypatch):
    heater1, heater2 = furnace.signals["heater1"], furnace.signals["heater2"]
    zone1, zone2 = furnace.signals["zone1"], furnace.signals["zone2"]
    rig.attach_controller(heater1, zone1, law=P(kp=1.0))
    grow = lambda: rig.attach_controller(heater2, zone2, law=P(kp=1.0))  # noqa: E731
    of = ControllerOut.of
    monkeypatch.setattr(ControllerOut, "of", _first_call(of, grow))
    response = client.get("/api/controllers")
    assert response.status_code == 200, response.text
    assert [c["name"] for c in response.json()] == [heater1.address]
    assert heater2.address in rig.controllers


def test_read_controller_schema_lists_a_snapshot(client, rig, furnace, fresh):
    """A device's signals are read inside the loop over the rig's devices: grow it there."""
    added = fresh("added")

    class Growing(Furnace):
        grow: Callable[[], object] | None = None

        @property
        def signals(self) -> dict[str, Any]:  # type: ignore[override]
            if (grow := self.grow) is not None:
                self.grow = None
                grow()
            return self._signals

        @signals.setter
        def signals(self, value: dict[str, Any]) -> None:
            self._signals = value

    growing = Growing(fresh("growing"))
    rig.add_device(growing)
    growing.grow = lambda: rig.add_device(Furnace(added))
    response = client.get("/api/controllers/schema")
    assert response.status_code == 200, response.text
    assert added in rig.devices
    assert not any(t["address"].startswith(f"{added}.") for t in response.json()["outputs"])


@pytest.mark.parametrize("call", ["document", "attach_controller"])
def test_the_rig_reads_and_changes_its_composition_under_its_lock(rig, furnace, call):
    """Another thread holding the rig lock (a delivery, a composition route) holds these off."""
    heater1, zone1 = furnace.signals["heater1"], furnace.signals["zone1"]
    run = {
        "document": rig.document,
        "attach_controller": lambda: rig.attach_controller(heater1, zone1, law=P(kp=1.0)),
    }[call]
    held, release, done = threading.Event(), threading.Event(), threading.Event()

    def hold() -> None:
        with rig.lock:
            held.set()
            release.wait(2.0)

    def call_it() -> None:
        run()
        done.set()

    threading.Thread(target=hold, daemon=True).start()
    assert held.wait(1.0)
    threading.Thread(target=call_it, daemon=True).start()
    waited = not done.wait(0.2)
    release.set()
    assert done.wait(1.0), f"{call} finished once the lock was free"
    assert waited, f"{call} waited for the rig lock"
