"""A client for a running rig, built from the schema it publishes.

`Rig` fetches `/api/schema` once and synthesises a method per device command,
validating arguments against the schema before sending:

    rig = Rig("http://pi:8000")
    rig.devices.pumps.set_fraction(wet_fraction=0.25, flow={"tag": "absolute", "flow": 8})
    rig.devices.sht4x.view()["conditions"]
    rig.demand("heaters.heater1", 1200.0)
    for frame in rig.watch("controllers"):
        ...

The CLI is this with argparse in front.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

from .validate import SchemaError, validate

__all__ = ["Device", "Devices", "Rig", "RigError", "SchemaError", "Unreachable"]


class RigError(Exception):
    """The rig refused: the server's own explanation."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


class Unreachable(RigError):
    def __init__(self, url: str, cause: Exception) -> None:
        super().__init__(0, f"no runner at {url} ({type(cause).__name__})")


class Device:
    """One device: its schema, its view, and a method per command."""

    def __init__(self, rig: Rig, name: str, schema: dict[str, Any]) -> None:
        self._rig = rig
        self.name = name
        self.schema = schema

    @property
    def commands(self) -> dict[str, dict[str, Any]]:
        return self.schema["commands"]

    def view(self) -> dict[str, Any]:
        """The device's tree with its current values, inputs, commands and conditions."""
        return self._rig.get(f"/api/devices/{self.name}")

    def run(self, command: str, **arguments: Any) -> Any:
        """Run a command, checking the arguments against its schema first."""
        try:
            spec = self.commands[command]
        except KeyError:
            raise SchemaError(
                f"{self.name} has no command {command!r}; it has {sorted(self.commands)}"
            ) from None
        validate(spec["arguments"], arguments, where=f"{self.name}.{command}")
        return self._rig.post(f"/api/devices/{self.name}/commands/{command}", arguments)

    def __getattr__(self, command: str) -> Any:
        if command.startswith("_") or command not in self.schema.get("commands", {}):
            raise AttributeError(command)
        return lambda **arguments: self.run(command, **arguments)

    def __dir__(self) -> list[str]:
        return [*super().__dir__(), *self.commands]

    def __repr__(self) -> str:
        return f"<{self.schema['type']} {self.name!r}: {', '.join(self.commands) or 'no commands'}>"


class Devices:
    """The rig's devices, by name and by attribute."""

    def __init__(self, rig: Rig) -> None:
        self._rig = rig

    def _schemas(self) -> dict[str, dict[str, Any]]:
        return self._rig.schema["devices"]

    def names(self) -> list[str]:
        return list(self._schemas())

    def __getitem__(self, name: str) -> Device:
        try:
            return Device(self._rig, name, self._schemas()[name])
        except KeyError:
            raise SchemaError(f"no device {name!r}; the rig has {self.names()}") from None

    def __getattr__(self, name: str) -> Device:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __iter__(self) -> Iterator[Device]:
        return (self[name] for name in self.names())

    def __dir__(self) -> list[str]:
        return [*super().__dir__(), *self.names()]


class Rig:
    """A running rig, over HTTP. `schema` may be given to work from a saved one.

    `token` is sent as a bearer token when the runner was started with one;
    `FLYBALL_TOKEN` in the environment is the default.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:8000",
        timeout: float = 5.0,
        schema: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._schema = schema
        self.token = os.environ.get("FLYBALL_TOKEN") if token is None else token

    # region Transport

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        import httpx  # the one dependency, imported here so the schema-only paths need nothing

        try:
            response = httpx.request(
                method, self.url + path, json=body, timeout=self.timeout, headers=self.headers
            )
        except httpx.HTTPError as e:
            raise Unreachable(self.url, e) from e
        if response.status_code >= 400:
            raise RigError(response.status_code, _detail(response))
        return response.json() if response.content else None

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: Any = None) -> Any:
        return self._request("POST", path, body)

    def put(self, path: str, body: Any = None) -> Any:
        return self._request("PUT", path, body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # endregion
    # region Schema

    @property
    def schema(self) -> dict[str, Any]:
        """`/api/schema`, fetched once."""
        if self._schema is None:
            self._schema = self.get("/api/schema")
        return self._schema

    def refresh(self) -> dict[str, Any]:
        self._schema = None
        return self.schema

    # endregion
    # region Surfaces

    @property
    def devices(self) -> Devices:
        return Devices(self)

    def controllers(self) -> Any:
        """Every controller, by its target address."""
        return self.get("/api/controllers")

    def read(self, address: str, fresh: bool = False) -> Any:
        """A signal's reading, a namespace's sample, or a device's samples."""
        query = "?fresh=true" if fresh else ""
        return self.get(f"/api/read/{address}{query}")

    def demand(self, address: str, value: float) -> Any:
        """Put `value` on the single writable signal at `address`."""
        return self.put(f"/api/signals/{address}", value)

    def waits(self) -> dict[str, Any]:
        """What the rig is waiting on, by name."""
        return self.get("/api/waits")

    def fire(self, name: str) -> bool:
        return bool(self.post(f"/api/waits/{name}/fire")["fired"])

    def interrupt(self, name: str) -> bool:
        return bool(self.post(f"/api/waits/{name}/interrupt")["interrupted"])

    def clock(self) -> dict[str, Any]:
        """The rig's timebase: `start_time_ns`, `now_ns`, `elapsed_ns`, `tags`, `speed`."""
        return self.get("/api/clock")

    def sim(self) -> dict[str, Any]:
        """A simulated rig's knobs (`/api/sim`); `{"simulated": false}` on hardware."""
        return self.get("/api/sim")

    def watch(self, stream: str) -> Iterator[dict[str, Any]]:
        """Frames from `/ws/<stream>`: samples, controllers, writes, signals."""
        from websockets.sync.client import connect

        ws_url = self.url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        with connect(f"{ws_url}/ws/{stream}", additional_headers=self.headers) as socket:
            for message in socket:
                yield json.loads(message)

    # endregion


def _detail(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text.strip()}"
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return f"HTTP {response.status_code}: {payload}"
