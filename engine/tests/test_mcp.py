"""The MCP server: the tiers a mode lists, and each tool as one call on the rig."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import types
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from flyball.interfaces.client import Rig as Client
from flyball.interfaces.client import RigError
from flyball.interfaces.mcp import Tier, tools_for
from flyball.interfaces.mcp.server import build
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_rig_config, set_store
from flyball.model.catalog import get_catalog
from flyball.record.sqlite import SqliteStore
from flyball.sequencing.command import Command
from test_server import Daq, Drive


class InProcess(Client):
    """The client over a `TestClient` rather than a socket."""

    def __init__(self, http: TestClient) -> None:
        super().__init__("http://test")
        self.http = http

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        response = self.http.request(method, path, json=body)
        if response.status_code >= 400:
            from flyball.interfaces.client.rig import RigError

            raise RigError(response.status_code, response.json().get("detail", response.text))
        return response.json() if response.content else None


@pytest.fixture
def client(tmp_path, rig, fresh):
    rig.name = "t"
    rig.add_device(Daq("furnace", label="Tube furnace"))
    rig.add_device(Drive("heaters"))
    store = SqliteStore(tmp_path / "t.db")
    set_rig(rig)
    set_store(store)
    with TestClient(create_app()) as http:
        yield InProcess(http)
    set_rig(None)
    set_store(None)


@pytest.fixture
def setpoint(fresh) -> str:
    """One program command, under a tag of its own."""
    tag = fresh("setpoint")

    @dataclass(frozen=True)
    class Setpoint(Command, tag=tag, primary="at"):
        at: float

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    get_catalog().register_command(Setpoint)
    return tag


def names(tools) -> set[str]:
    return {t.name for t in tools}


class TestModes:
    def test_read_lists_only_reads(self, client):
        tools = tools_for(client, "read")
        assert all(t.tier == Tier.READ for t in tools)
        assert {
            "status",
            "describe_device",
            "check_program",
            "check_rig",
            "widget_schema",
        } <= names(tools)

    def test_author_adds_the_store_and_nothing_that_moves(self, client):
        author = names(tools_for(client, "author"))
        assert {"save_program", "update_dashboard", "save_tuning"} <= author
        assert not {"set_demand", "run_program", "heaters-set_duty"} & author

    def test_operate_adds_the_rig_s_own_commands(self, client):
        operate = names(tools_for(client, "operate"))
        assert {
            "set_demand",
            "regulate",
            "run_program",
            "heaters-set_duty",
            "furnace-restore",
        } <= operate
        assert "heaters-off" not in operate, "a simulation-only command is hidden on hardware"
        assert "sim_clock" not in operate

    def test_a_device_tool_carries_the_rig_s_schema_and_flags(self, client):
        tool = next(t for t in tools_for(client, "operate") if t.name == "heaters-set_duty")
        assert tool.tier == Tier.DRIVE and not tool.destructive
        assert tool.schema["properties"]["duty"]["type"] == "number"
        assert tool.description.startswith("[heaters] Drive the elements")


class TestTools:
    def tool(self, client, name):
        return next(t for t in tools_for(client, "operate") if t.name == name)

    def test_reads(self, client):
        assert self.tool(client, "status").run(client, {})["rig"] == "t"
        devices = self.tool(client, "list_devices").run(client, {})
        assert [d["name"] for d in devices] == ["furnace", "heaters"]
        schema = self.tool(client, "describe_device").run(client, {"name": "heaters"})
        assert "set_duty" in schema["commands"]
        kinds = self.tool(client, "widget_schema").run(client, {})["kinds"]
        assert [k["kind"] for k in kinds][:3] == ["readout", "gauge", "chart"]

    def test_a_device_command_runs_and_is_validated(self, client):
        from flyball.interfaces.client import SchemaError

        tool = self.tool(client, "heaters-set_duty")
        assert tool.run(client, {"duty": 0.4}) == 0.4
        with pytest.raises(SchemaError, match="missing"):
            tool.run(client, {})

    def test_programs_save_check_and_list(self, client, setpoint):
        program = {"name": "warm", "steps": [{setpoint: 50}]}
        assert self.tool(client, "check_program").run(client, {"document": program})["ok"]
        saved = self.tool(client, "save_program").run(
            client, {"name": "warm", "document": program, "label": "v1"}
        )
        assert saved["format"] == "json" and json.loads(saved["body"]) == program
        assert [p["name"] for p in self.tool(client, "list_programs").run(client, {})] == ["warm"]

    def test_update_dashboard_changes_parts_and_versions(self, client):
        document = {
            "name": "d",
            "rig": "t",
            "widgets": [
                {
                    "id": "a",
                    "kind": "readout",
                    "x": 0,
                    "y": 0,
                    "w": 6,
                    "h": 5,
                    "config": {"address": "furnace.zone1"},
                }
            ],
        }
        self.tool(client, "save_dashboard").run(client, {"name": "d", "document": document})
        result = self.tool(client, "update_dashboard").run(
            client,
            {
                "name": "d",
                "changes": [
                    {"op": "move_widget", "id": "a", "x": 6},
                    {"op": "set_widget_config", "id": "a", "config": {"sparkline": False}},
                    {
                        "op": "add_widget",
                        "widget": {
                            "id": "b",
                            "kind": "gauge",
                            "x": 0,
                            "y": 5,
                            "w": 6,
                            "h": 6,
                            "config": {"address": "nowhere"},
                        },
                    },
                ],
            },
        )
        widgets = {w["id"]: w for w in result["body"]["widgets"]}
        assert widgets["a"]["x"] == 6 and widgets["a"]["config"] == {
            "address": "furnace.zone1",
            "sparkline": False,
        }
        assert [p["widget_id"] for p in result["problems"]] == ["b"]
        assert len(client.get("/api/dashboards/d/history")) == 2

    def test_update_dashboard_refuses_an_unknown_widget(self, client):
        from flyball.interfaces.client import SchemaError

        self.tool(client, "save_dashboard").run(
            client, {"name": "d", "document": {"name": "d", "rig": "t"}}
        )
        with pytest.raises(SchemaError, match="no widget 'zz'"):
            self.tool(client, "update_dashboard").run(
                client, {"name": "d", "changes": [{"op": "remove_widget", "id": "zz"}]}
            )


class TestRigRoutes:
    def test_check_validates_and_canonicalises(self, client):
        out = client.post("/api/rig/check", {"name": "x", "devices": {}})
        assert out["name"] == "x"
        from flyball.interfaces.client.rig import RigError

        with pytest.raises(RigError, match="devices"):
            client.post("/api/rig/check", {"devices": "no"})

    def test_config_is_what_the_runner_was_given(self, client):
        from flyball.interfaces.client.rig import RigError
        from flyball.runtime.config import RigConfig

        with pytest.raises(RigError, match="not started from a rig file"):
            client.get("/api/rig/config")
        set_rig_config(RigConfig.model_validate({"name": "hw"}))
        try:
            assert client.get("/api/rig/config")["name"] == "hw"
        finally:
            set_rig_config(None)

    def test_schema_is_the_rig_file_s(self, client):
        assert "devices" in client.get("/api/rig/schema")["properties"]


class TestOverTheWire:
    async def test_list_and_call_through_a_session(self, client):
        server = build(client, "author")
        async with create_client_server_memory_streams() as (client_streams, server_streams):

            async def serve() -> None:
                await server.run(*server_streams, server.create_initialization_options())

            async with anyio.create_task_group() as tg:
                tg.start_soon(serve)
                async with ClientSession(*client_streams) as session:
                    init = await session.initialize()
                    assert "mode `author`" in (init.instructions or "")
                    listed = await session.list_tools()
                    by_name = {t.name: t for t in listed.tools}
                    assert by_name["status"].annotations.read_only_hint is True
                    assert by_name["delete_program"].annotations.destructive_hint is True
                    assert "set_demand" not in by_name
                    result = await session.call_tool("view_device", {"name": "heaters"})
                    assert not result.is_error
                    assert isinstance(result.content[0], types.TextContent)
                    failed = await session.call_tool("get_program", {"name": "nope"})
                    assert failed.is_error
                tg.cancel_scope.cancel()


class TestMounted:
    """`/mcp/<mode>` in the runner's app: the JSON-RPC exchange a client makes, by hand."""

    @pytest.fixture
    def http(self, tmp_path, rig):
        from flyball.interfaces.mcp.http import mount

        rig.name = "t"
        rig.add_device(Drive("heaters"))
        set_rig(rig)
        set_store(SqliteStore(tmp_path / "t.db"))
        app = create_app()
        http = TestClient(app)
        mount(app, InProcess(http))  # the tools call back into the same app
        with http:
            yield http
        set_rig(None)
        set_store(None)

    def rpc(self, http, mode, method, params=None, session=None, id=1):
        headers = {"Accept": "application/json, text/event-stream"}
        if session:
            headers["mcp-session-id"] = session
        body = {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}
        return http.post(f"/mcp/{mode}", json=body, headers=headers)

    def test_initialize_list_and_call(self, http):
        init = self.rpc(
            http,
            "read",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        )
        assert init.status_code == 200, init.text
        assert "mode `read`" in init.json()["result"]["instructions"]
        session = init.headers["mcp-session-id"]
        http.post(
            "/mcp/read",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"mcp-session-id": session, "Accept": "application/json, text/event-stream"},
        )
        listed = self.rpc(http, "read", "tools/list", session=session, id=2).json()["result"]
        names = {t["name"] for t in listed["tools"]}
        assert "status" in names and "set_demand" not in names
        called = self.rpc(
            http,
            "read",
            "tools/call",
            {"name": "list_devices", "arguments": {}},
            session=session,
            id=3,
        ).json()["result"]
        assert not called.get("isError") and "heaters" in called["content"][0]["text"]

    def test_each_mode_has_a_route(self, http):
        for mode in ("read", "author", "operate"):
            assert self.rpc(http, mode, "ping").status_code in (200, 400), mode

    def initialize(self, http, mode):
        init = self.rpc(
            http,
            mode,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        )
        session = init.headers["mcp-session-id"]
        http.post(
            f"/mcp/{mode}",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"mcp-session-id": session, "Accept": "application/json, text/event-stream"},
        )
        return init, session

    def test_a_missing_required_argument_is_invalid_params_not_a_keyerror(self, http):
        _, session = self.initialize(http, "read")
        body = self.rpc(
            http,
            "read",
            "tools/call",
            {"name": "describe_device", "arguments": {}},
            session=session,
            id=2,
        ).json()
        assert "result" not in body, body
        assert body["error"]["code"] == -32602
        assert "name" in body["error"]["message"]
        assert "KeyError" not in body["error"]["message"]


class TestDriverTools:
    def tool(self, client, name, mode="operate"):
        return next(t for t in tools_for(client, mode) if t.name == name)

    def test_tools_whose_routes_the_runner_lacks_are_not_listed(self, client, monkeypatch):
        from flyball.interfaces.mcp import tools

        gated = {t.name for t in tools.DRIVERS if t.route is not None}
        monkeypatch.setattr(tools, "_served", lambda rig: set())
        listed = names(tools_for(client, "operate"))
        assert {"driver_guide", "driver_scaffold", "check_driver"} <= listed
        assert not gated & listed
        monkeypatch.setattr(tools, "_served", lambda rig: {("post", "/api/devices")})
        assert gated & names(tools_for(client, "operate")) == {"attach_device"}
        assert "check_driver" not in names(tools_for(client, "author")), "imports a file: drive"
        assert "search_drivers" not in names(tools_for(client, "author")), "runs a script: drive"

    def test_served_is_read_from_the_runner_s_openapi(self, client):
        from flyball.interfaces.mcp.tools import _served

        assert ("get", "/api/devices/{name}/schema") in _served(client)

    def test_the_runner_s_driver_routes_list_their_tools(self, client):
        listed = names(tools_for(client, "operate"))
        assert {"list_drivers", "reload_drivers", "probe_hardware", "link_query"} <= listed
        drivers = self.tool(client, "list_drivers", "read").run(client, {})
        assert drivers["sim_drive"]["role"] == "driver" and "schema" in drivers["sim_drive"]
        with pytest.raises(RigError, match="no drivers directory"):
            self.tool(client, "reload_drivers").run(client, {})

    def test_guide_and_scaffold(self, client):
        guide = self.tool(client, "driver_guide", "read").run(client, {})
        assert guide.startswith("# Writing a device driver")
        out = self.tool(client, "driver_scaffold", "read").run(client, {"name": "foo-200"})
        assert 'tag="foo_200"' in out["source"]
        from flyball.interfaces.client import SchemaError

        with pytest.raises(SchemaError, match="identifier"):
            self.tool(client, "driver_scaffold", "read").run(client, {"name": "1"})

    def test_check_driver_imports_a_scaffold_and_reports(self, client, tmp_path, fresh):
        name = fresh("gadget")
        path = tmp_path / f"{name}.py"
        path.write_text(
            self.tool(client, "driver_scaffold", "read").run(client, {"name": name})["source"]
        )
        report = self.tool(client, "check_driver").run(client, {"path": str(path)})
        assert report["ok"], report
        (driver,) = report["drivers"]
        assert driver["tag"] == name and driver["readable"] and not driver["writable"]
        assert driver["descriptors"] == ["conditions", "value"] and driver["commands"] == ["reset"]
        assert "properties" in driver["schema"]

    def test_check_driver_reports_a_broken_module(self, client, tmp_path):
        path = tmp_path / "broken.py"
        path.write_text("import flyball.foundation.device\nraise RuntimeError('no such bus')\n")
        report = self.tool(client, "check_driver").run(client, {"path": str(path)})
        assert not report["ok"] and "no such bus" in report["errors"][0]
        path.write_text("x = 1\n")
        report = self.tool(client, "check_driver").run(client, {"path": str(path)})
        assert not report["ok"] and "registers no DriverConfig" in report["errors"][0]

    async def test_guide_is_a_resource_and_tool_changes_are_announced(self, client):
        server = build(client, "read")
        assert server.create_initialization_options().capabilities.tools.list_changed
        async with create_client_server_memory_streams() as (client_streams, server_streams):

            async def serve() -> None:
                await server.run(*server_streams, server.create_initialization_options())

            async with anyio.create_task_group() as tg:
                tg.start_soon(serve)
                async with ClientSession(*client_streams) as session:
                    await session.initialize()
                    listed = await session.list_resources()
                    assert [str(r.uri) for r in listed.resources] == ["flyball://guide/driver"]
                    read = await session.read_resource("flyball://guide/driver")
                    assert "attach_device" in read.contents[0].text
                tg.cancel_scope.cancel()


class TestComposition:
    """Building a rig up through the tools: what an agent does from nothing."""

    def tool(self, client, name):
        return next(t for t in tools_for(client, "operate") if t.name == name)

    def test_link_device_document_and_changes(self, client):
        assert {"attach_link", "attach_device", "attach_document", "rig_versions"} <= names(
            tools_for(client, "operate")
        )
        assert "spare" not in self.tool(client, "rig_document").run(client, {})["devices"]
        self.tool(client, "attach_link").run(
            client, {"name": "plant", "config": {"tag": "sim_furnace"}}
        )
        added = self.tool(client, "attach_device").run(
            client,
            {
                "name": "spare",
                "entry": {"driver": "sim_drive", "link": "plant", "ports": {"power": "heater1"}},
            },
        )
        assert added["name"] == "spare"
        client.refresh()  # the server does this after a tool that changes the rig
        assert "spare" in client.schema["devices"]
        assert "spare-disturb" not in names(tools_for(client, "operate")), "simulation-only: hidden"
        assert isinstance(self.tool(client, "rig_changes").run(client, {}), dict)
        self.tool(client, "detach_device").run(client, {"name": "spare"})
        assert "spare" not in self.tool(client, "rig_document").run(client, {})["devices"]
        # Versions and restore need the runner's change hook on the store: test_composition.

    async def test_attaching_announces_a_new_tool_list(self, client):
        server = build(client, "operate")
        seen: list[str] = []

        async def handler(message: Any) -> None:
            seen.append(type(message).__name__)

        async with create_client_server_memory_streams() as (client_streams, server_streams):

            async def serve() -> None:
                await server.run(*server_streams, server.create_initialization_options())

            async with anyio.create_task_group() as tg:
                tg.start_soon(serve)
                async with ClientSession(*client_streams, message_handler=handler) as session:
                    await session.initialize()
                    await session.list_tools()
                    await session.call_tool(
                        "attach_link", {"name": "plant", "config": {"tag": "sim_furnace"}}
                    )
                    result = await session.call_tool(
                        "attach_device",
                        {
                            "name": "spare",
                            "entry": {
                                "driver": "sim_drive",
                                "link": "plant",
                                "ports": {"power": "heater1"},
                            },
                        },
                    )
                    assert not result.is_error, result.content
                    assert "ToolListChangedNotification" in seen
                    described = await session.call_tool("describe_device", {"name": "spare"})
                    assert not described.is_error
                tg.cancel_scope.cancel()
