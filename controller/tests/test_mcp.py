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

from flyball.client import Rig as Client
from flyball.db.sqlite import SqliteStore
from flyball.mcp import Tier, tools_for
from flyball.mcp.server import build
from flyball.programmer.command import Command
from flyball.server import create_app, set_rig
from flyball.server.deps import set_rig_config, set_store
from test_server import Daq, Drive


class InProcess(Client):
    """The client over a `TestClient` rather than a socket."""

    def __init__(self, http: TestClient) -> None:
        super().__init__("http://test")
        self.http = http

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        response = self.http.request(method, path, json=body)
        if response.status_code >= 400:
            from flyball.client.rig import RigError

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
        from flyball.client import SchemaError

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
        from flyball.client import SchemaError

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
        from flyball.client.rig import RigError

        with pytest.raises(RigError, match="devices"):
            client.post("/api/rig/check", {"devices": "no"})

    def test_config_is_what_the_daemon_was_given(self, client):
        from flyball.client.rig import RigError
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
    """`/mcp/<mode>` in the daemon's app: the JSON-RPC exchange a client makes, by hand."""

    @pytest.fixture
    def http(self, tmp_path, rig):
        from flyball.mcp.http import mount

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
