"""The MCP server: what each tier lists, and each tool as one call on the rig."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from conftest import FakeRunner, TestClient, free_port
from flyball.interfaces.client import Rig as Client
from flyball.interfaces.client import RigError
from flyball.interfaces.mcp import Tier, tools_for
from flyball.interfaces.mcp.server import build
from flyball.interfaces.server import create_app, set_rig
from flyball.interfaces.server.deps import set_rig_config, set_runner, set_store
from flyball.model.catalog import get_catalog
from flyball.record.sqlite import SqliteStore
from flyball.sequencing.step import Step
from test_server import Daq, Drive


class InProcess(Client):
    """The client over a `TestClient` rather than a socket."""

    def __init__(self, http: TestClient) -> None:
        super().__init__("http://test")
        self.http = http

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        response = self.http.request(method, path, json=body, headers=self.headers)
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
    """One program command, under a type of its own."""
    type_ = fresh("setpoint")

    @dataclass(frozen=True)
    class Setpoint(Step, type=type_, primary="at"):
        at: float

        def run(self, rig: Any, operator: Any = None) -> Any: ...

    get_catalog().register_step(Setpoint)
    return type_


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

    def test_read_tier_does_not_expose_the_side_effecting_flags(self, client):
        """Neither `read`'s `fresh` nor `probe_hardware`'s `scan` belongs where nothing changes."""
        read = tools_for(client, "read")
        by_name = {t.name: t for t in read}
        assert "fresh" not in by_name["read"].schema["properties"]
        assert "fresh" not in by_name["read_many"].schema["properties"]
        assert "scan" not in by_name["probe_hardware"].schema["properties"]

    def test_operate_has_the_full_power_forms(self, client):
        operate = tools_for(client, "operate")
        by_name = {t.name: t for t in operate}
        assert "fresh" in by_name["read"].schema["properties"]
        assert by_name["read"].tier == Tier.DRIVE
        assert "fresh" in by_name["read_many"].schema["properties"]
        assert "scan" in by_name["probe_hardware"].schema["properties"]
        assert by_name["probe_hardware"].tier == Tier.DRIVE

    def test_author_adds_the_store_and_nothing_that_moves(self, client):
        author = names(tools_for(client, "author"))
        assert {"save_program", "update_dashboard", "save_tuning"} <= author
        assert not {"write", "run_program", "heaters-set_duty"} & author

    def test_operate_adds_the_rig_s_own_commands(self, client):
        operate = names(tools_for(client, "operate"))
        assert {
            "write",
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
        assert tool.description.startswith("[Heaters] Drive the elements")


class TestTools:
    def tool(self, client, name):
        return next(t for t in tools_for(client, "operate") if t.name == name)

    def test_reads(self, client):
        assert self.tool(client, "status").run(client, {})["rig"] == "t"
        devices = self.tool(client, "list_devices").run(client, {})["devices"]
        assert [d["name"] for d in devices] == ["furnace", "heaters"]
        assert set(devices[0]) == {"name", "class_name", "label", "description"}, (
            "projected: not the full tree"
        )
        detailed = self.tool(client, "list_devices").run(client, {"verbose": True})["devices"]
        assert "signals" in detailed[0], "`verbose` asked for the rest"
        schema = self.tool(client, "describe_device").run(client, {"name": "heaters"})
        assert "set_duty" in schema["commands"]
        types = self.tool(client, "widget_schema").run(client, {})["types"]
        assert [t["type"] for t in types][:3] == ["readout", "gauge", "chart"]

    @pytest.mark.parametrize("name", ["..", ".", "", "../health", "heaters/../../health"])
    def test_a_name_cannot_climb_to_another_route(self, client, name):
        """Httpx collapses `..` in a path, so `/api/devices/../health` would be `/api/health`."""
        from flyball.interfaces.client import SchemaError

        for tool, arguments in (
            ("view_device", {"name": name}),
            ("describe_device", {"name": name}),
            ("read", {"address": name}),
            ("manual", {"controller": name}),
        ):
            with pytest.raises(SchemaError, match="not a name"):
                self.tool(client, tool).run(client, arguments)

    def test_a_name_is_one_path_segment_whatever_it_holds(self, client):
        """`?`, `#` and `%` are encoded: a name cannot add a query or cut the path short."""
        for name in ("heaters?fresh=true", "heaters#x", "heaters%"):
            with pytest.raises(RigError) as refused:
                self.tool(client, "view_device").run(client, {"name": name})
            assert refused.value.status == 404, name

    def test_a_device_command_runs_and_is_validated(self, client):
        from flyball.interfaces.client import SchemaError

        tool = self.tool(client, "heaters-set_duty")
        assert tool.run(client, {"duty": 0.4}) == {"result": 0.4, "interrupted": []}
        with pytest.raises(SchemaError, match="missing"):
            tool.run(client, {})

    def test_programs_save_check_and_list(self, client, setpoint):
        program = {"name": "warm", "steps": [{setpoint: 50}]}
        assert self.tool(client, "check_program").run(client, {"document": program})["ok"]
        saved = self.tool(client, "save_program").run(
            client, {"name": "warm", "document": program, "label": "v1"}
        )
        assert saved["format"] == "json" and json.loads(saved["body"]) == program
        programs = self.tool(client, "list_programs").run(client, {})["programs"]
        assert [p["name"] for p in programs] == ["warm"]

    def test_update_dashboard_changes_parts_and_versions(self, client):
        document = {
            "name": "d",
            "rig": "t",
            "widgets": [
                {
                    "id": "a",
                    "type": "readout",
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
                    {"op": "set_widget_label", "id": "a", "label": "Zone 1"},
                    {"op": "set_label", "label": "Furnace"},
                    {
                        "op": "add_widget",
                        "widget": {
                            "id": "b",
                            "type": "gauge",
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
        assert widgets["a"]["label"] == "Zone 1" and result["body"]["label"] == "Furnace"
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

    def test_session_ticks_is_read_tier_and_hits_the_route(self, client):
        """A recorded controller's steps, served at `.../sessions/{id}/ticks/{controller}`."""
        tool = next(t for t in tools_for(client, "read") if t.name == "session_ticks")
        assert tool.tier == Tier.READ
        assert tool.schema["required"] == ["session_id", "controller"]

        class FakeRig:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def get(self, path: str) -> list[str]:
                self.calls.append(path)
                return ["a tick"]

        fake = FakeRig()
        result = tool.run(
            fake, {"session_id": 3, "controller": "heaters.heater1", "every": 5, "start_ns": 10}
        )
        assert result == {"ticks": ["a tick"]}
        assert tool.output_schema["required"] == ["ticks"]
        assert fake.calls == ["/api/history/sessions/3/ticks/heaters.heater1?start_ns=10&every=5"]


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


class TestInstructions:
    def test_names_the_rig_it_was_given_not_its_own_url(self, client):
        server = build(client, "operate", "chamber")
        assert "chamber" in (server.instructions or "")
        assert client.url not in (server.instructions or "")

    def test_falls_back_when_no_name_is_given(self, client):
        server = build(client, "operate")
        assert "This rig" in (server.instructions or "")
        assert client.url not in (server.instructions or "")


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
                    assert "tier `author`" in (init.instructions or "")
                    listed = await session.list_tools()
                    by_name = {t.name: t for t in listed.tools}
                    assert by_name["status"].annotations.read_only_hint is True
                    assert by_name["delete_program"].annotations.destructive_hint is True
                    assert "write" not in by_name
                    result = await session.call_tool("view_device", {"name": "heaters"})
                    assert not result.is_error
                    assert isinstance(result.content[0], types.TextContent)
                    failed = await session.call_tool("get_program", {"name": "nope"})
                    assert failed.is_error
                    assert by_name["controllers"].output_schema["required"] == ["controllers"]
                    listed_controllers = await session.call_tool("controllers", {})
                    assert listed_controllers.structured_content == {"controllers": []}
                tg.cancel_scope.cancel()


class TestMounted:
    """`/mcp/<tier>` in the runner's app: the JSON-RPC exchange a client makes, by hand."""

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

    def rpc(self, http, tier, method, params=None, session=None, id=1):
        headers = {"Accept": "application/json, text/event-stream"}
        if session:
            headers["mcp-session-id"] = session
        body = {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}
        return http.post(f"/mcp/{tier}", json=body, headers=headers)

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
        assert "tier `read`" in init.json()["result"]["instructions"]
        session = init.headers["mcp-session-id"]
        http.post(
            "/mcp/read",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"mcp-session-id": session, "Accept": "application/json, text/event-stream"},
        )
        listed = self.rpc(http, "read", "tools/list", session=session, id=2).json()["result"]
        names = {t["name"] for t in listed["tools"]}
        assert "status" in names and "write" not in names
        called = self.rpc(
            http,
            "read",
            "tools/call",
            {"name": "list_devices", "arguments": {}},
            session=session,
            id=3,
        ).json()["result"]
        assert not called.get("isError") and "heaters" in called["content"][0]["text"]

    def test_an_open_runner_s_mcp_refuses_a_rebound_name_itself(self, rig):
        """DNS rebinding protection is on in the MCP transport too, not only at the door.

        A bare app (no door in front) so the transport's own check is what answers.
        """
        from fastapi import FastAPI

        from flyball.interfaces.mcp.http import mount

        rig.name = "t"
        set_rig(rig)
        app = FastAPI()
        http = TestClient(app)
        mount(app, InProcess(http))
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        accept = {"Accept": "application/json, text/event-stream"}
        try:
            with http:
                rebound = http.post(
                    "/mcp/read", json=body, headers={**accept, "Host": "evil.example"}
                )
                assert rebound.status_code == 421, rebound.text
                foreign = http.post(
                    "/mcp/read", json=body, headers={**accept, "Origin": "http://evil.example"}
                )
                assert foreign.status_code == 403, foreign.text
                own = {**accept, "Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"}
                assert http.post("/mcp/read", json=body, headers=own).status_code != 421
        finally:
            set_rig(None)

    def test_an_insecure_open_runner_s_mcp_answers_its_network_name(self, rig):
        """`--insecure-open`: the transport's loopback-only check is off, as the door's is."""
        from flyball.interfaces.mcp.http import mount

        rig.name = "t"
        set_rig(rig)
        app = create_app(open_network=True)
        http = TestClient(app, base_url="http://192.168.1.3:8000")
        mount(app, InProcess(http))
        body = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        headers = {"Accept": "application/json, text/event-stream"}
        try:
            with http:
                lan = http.post("/mcp/read", json=body, headers=headers)
                assert lan.status_code not in (403, 421), lan.text
                foreign = {**headers, "Origin": "http://evil.example"}
                assert http.post("/mcp/read", json=body, headers=foreign).status_code == 403
        finally:
            set_rig(None)

    def test_each_mode_has_a_route(self, http):
        for tier in ("read", "author", "operate"):
            assert self.rpc(http, tier, "ping").status_code in (200, 400), tier

    def initialize(self, http, tier):
        init = self.rpc(
            http,
            tier,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        )
        session = init.headers["mcp-session-id"]
        http.post(
            f"/mcp/{tier}",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"mcp-session-id": session, "Accept": "application/json, text/event-stream"},
        )
        return init, session

    def test_initialize_reports_a_nonempty_version(self, http):
        init, _ = self.initialize(http, "read")
        assert init.json()["result"]["serverInfo"]["version"]

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
    def tool(self, client, name, tier="operate"):
        return next(t for t in tools_for(client, tier) if t.name == name)

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

    def test_probe_hardware_posts_at_every_tier(self, client):
        """A scan drives the bus, so `/api/probe` is a POST; the read tier never scans."""
        seen: list[tuple[str, str]] = []

        class Recorder:
            def get(self, path: str) -> Any:
                seen.append(("get", path))

            def post(self, path: str, body: Any = None) -> Any:
                seen.append(("post", path))

        self.tool(client, "probe_hardware", "read").run(Recorder(), {})
        self.tool(client, "probe_hardware").run(Recorder(), {"scan": True})
        assert seen == [("post", "/api/probe?scan=false"), ("post", "/api/probe?scan=true")]
        assert self.tool(client, "probe_hardware").route == ("post", "/api/probe")

    def test_guide_and_scaffold(self, client):
        guide = self.tool(client, "driver_guide", "read").run(client, {})
        assert guide.startswith("# Writing a device driver")
        out = self.tool(client, "driver_scaffold", "read").run(client, {"name": "foo-200"})
        assert 'type="foo_200"' in out["source"]
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
        assert driver["type"] == name and driver["readable"] and not driver["writable"]
        assert driver["descriptors"] == ["value"] and driver["commands"] == ["reset"]
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

    def test_an_edit_is_saved_and_restarts_the_rig(self, client, rig):
        assert {"attach_link", "attach_device", "attach_document", "rig_versions"} <= names(
            tools_for(client, "operate")
        )
        restore = self.tool(client, "restore_rig_version")
        assert "Not applied in place" in restore.description
        assert "restarts" in restore.description and "force" in restore.schema["properties"]
        runner = FakeRunner()
        set_runner(runner)
        try:
            out = self.tool(client, "attach_document").run(
                client,
                {
                    "document": {
                        "links": {"plant": {"type": "sim_furnace"}},
                        "devices": {
                            "spare": {
                                "driver": "sim_drive",
                                "link": "plant",
                                "ports": {"power": "heater1"},
                            }
                        },
                    }
                },
            )
            assert out["restarting"] is True and out["reason"].startswith("edited: added a doc")
            assert runner.edits == [(out["rig_version_id"], out["previous"], False)]
            assert "spare" not in self.tool(client, "rig_document").run(client, {})["devices"]
            saved = self.tool(client, "rig_version").run(
                client, {"version_id": out["rig_version_id"]}
            )
            assert "spare" in saved["document"]["devices"], "saved: the restart builds it"
            again = self.tool(client, "attach_link")
            with pytest.raises(RigError, match="restarting"):
                again.run(client, {"name": "p2", "config": {"type": "sim_furnace"}, "force": True})
        finally:
            set_runner(None)

    async def test_attaching_announces_a_new_tool_list(self, client):
        server = build(client, "operate")
        seen: list[str] = []

        async def handler(message: Any) -> None:
            seen.append(type(message).__name__)

        set_runner(FakeRunner())
        try:
            async with create_client_server_memory_streams() as (client_streams, server_streams):

                async def serve() -> None:
                    await server.run(*server_streams, server.create_initialization_options())

                async with anyio.create_task_group() as tg:
                    tg.start_soon(serve)
                    async with ClientSession(*client_streams, message_handler=handler) as session:
                        await session.initialize()
                        await session.list_tools()
                        result = await session.call_tool(
                            "attach_document",
                            {
                                "document": {
                                    "links": {"plant": {"type": "sim_furnace"}},
                                    "devices": {
                                        "spare": {
                                            "driver": "sim_drive",
                                            "link": "plant",
                                            "ports": {"power": "heater1"},
                                        }
                                    },
                                }
                            },
                        )
                        assert not result.is_error, result.content
                        assert "ToolListChangedNotification" in seen
                    tg.cancel_scope.cancel()
        finally:
            set_runner(None)


# region The re-mint: an MCP tool's inner call carries its caller, capped to the tier


class Spy:
    """ASGI: the `X-Flyball-Principal` of every request that is not an MCP request itself."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.inner: list[tuple[str, str | None]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and "/mcp/" not in scope["path"]:
            headers = dict(scope.get("headers") or [])
            token = headers.get(b"x-flyball-principal")
            self.inner.append((scope["path"], None if token is None else token.decode()))
        await self.app(scope, receive, send)


@dataclass
class Served:
    """A runner's app with MCP mounted as `serve` mounts it, serving on a real socket."""

    app: Any
    spy: Spy
    base: str
    uds: str | None
    key: bytes
    aud: str
    fronted: bool
    token: str | None = None

    def http(self) -> Any:
        import httpx

        transport = httpx.HTTPTransport(uds=self.uds) if self.uds else None
        return httpx.Client(base_url=self.base, transport=transport, timeout=10)

    def caller(self, scp: set[str], sid: str = "s-caller") -> dict[str, str]:
        """The credential the outer request carries: a front's principal, or the token."""
        from flyball.interfaces.server import principal

        if not self.fronted:
            return {"Authorization": f"Bearer {self.token}"}
        now = int(time.time())
        claims = principal.Claims(
            sub="token:ci",
            sid=sid,
            scp=frozenset(scp),
            kind="agent",
            aud=self.aud,
            cip="192.0.2.7",
            sch="https",
            iat=now,
            exp=now + principal.LIFETIME,
            nm="CI",
        )
        return {"X-Flyball-Principal": principal.mint(self.key, claims)}

    def session(self, http: Any, tier: str, auth: dict[str, str]) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream", **auth}
        init = http.post(
            f"/mcp/{tier}",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
            headers=headers,
        )
        assert init.status_code == 200, init.text
        headers["mcp-session-id"] = init.headers["mcp-session-id"]
        http.post(
            f"/mcp/{tier}",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )
        return headers

    def call(
        self, tier: str, tool: str, arguments: dict[str, Any] | None = None, *, scp: set[str]
    ) -> dict[str, Any]:
        with self.http() as http:
            headers = self.session(http, tier, self.caller(scp))
            body = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
            answer = http.post(f"/mcp/{tier}", json=body, headers=headers)
            assert answer.status_code == 200, answer.text
            return answer.json()["result"]

    def listed(self, tier: str, *, scp: set[str]) -> set[str]:
        with self.http() as http:
            headers = self.session(http, tier, self.caller(scp))
            body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            answer = http.post(f"/mcp/{tier}", json=body, headers=headers)
            return {t["name"] for t in answer.json()["result"]["tools"]}

    def minted(self) -> list[Any]:
        """The claims of every inner call so far, verified as the door did when it came."""
        import base64

        from flyball.interfaces.server import principal

        def iat(token: str) -> int:
            payload = token.split(".")[1]
            return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["iat"]

        return [
            principal.verify(token, self.key, self.aud, now=iat(token))
            for path, token in self.spy.inner
            if token is not None
        ]


@pytest.fixture(params=["fronted", "bare"])
def served(request, rig, tmp_path) -> Any:
    """`serve`'s app and MCP mount, fronted over a unix socket or bare over loopback TCP."""
    import uvicorn

    from flyball.interfaces.server.auth import Fronted
    from flyball.runner.frontdir import FrontDir
    from flyball.runner.serving import mount_mcp
    from flyball.runtime.config import AuthConfig, RunnerConfig

    rig.name = "t"
    rig.add_device(Drive("heaters"))
    set_rig(rig)
    set_store(SqliteStore(tmp_path / "t.db"))
    fronted = request.param == "fronted"
    folder = Path(tempfile.mkdtemp(prefix="a6-"))
    if fronted:
        key, aud = os.urandom(32), "rig-a6"
        sock = str(folder / "endpoint.sock")
        front = FrontDir(folder, key, aud, f"unix:{sock}")
        settings = RunnerConfig()
        app = create_app(front=Fronted(key, aud))
        bind: dict[str, Any] = {"uds": sock}
        base, uds, token = "http://localhost", sock, None
    else:
        front, token, port = None, "s3cret-token", free_port()
        settings = RunnerConfig(port=port, auth=AuthConfig(token=token))
        app = create_app(settings.auth, port=port, login_delay=0)
        bind = {"host": "127.0.0.1", "port": port}
        base, uds = f"http://127.0.0.1:{port}", None
    mount_mcp(app, "t", settings, front)
    door = app.state.door
    spy = Spy(app)
    server = uvicorn.Server(uvicorn.Config(spy, log_level="warning", **bind))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the server did not start"
        time.sleep(0.02)
    try:
        yield Served(app, spy, base, uds, door.key, door.aud, fronted, token)
    finally:
        server.should_exit = True
        thread.join(10)
        set_rig(None)
        set_store(None)
        shutil.rmtree(folder, ignore_errors=True)


def _sneak(rig: Any, a: dict[str, Any]) -> Any:
    """A read-tier tool that acts anyway: what the re-mint is there to catch."""
    return rig.post("/api/devices/heaters/commands/set_duty", {"duty": 0.5})


def _twice(rig: Any, a: dict[str, Any]) -> Any:
    """Two inner calls with more than a principal's lifetime between them."""
    first = rig.get("/api/health")
    _CLOCK[0] += 70
    second = rig.get("/api/health")
    return {"first": first["rig"], "second": second["rig"]}


_CLOCK = [0.0]


@pytest.fixture
def extra_tools(monkeypatch):
    from flyball.interfaces.mcp import tools

    extra = (
        tools.Tool("sneak", "Acts from the read tier.", tools._object(), Tier.READ, _sneak),
        tools.Tool("twice", "Two calls, 70 s apart.", tools._object(), Tier.READ, _twice),
    )
    monkeypatch.setattr(tools, "READ", (*tools.READ, *extra))


class TestReMint:
    def test_inner_call_carries_caller(self, served):
        """`sub`/`sid`/`kind` are the caller's, `via` is mcp, `scp` is caller ∩ the tier."""
        result = served.call("read", "status", scp={"read", "operate"})
        assert not result.get("isError"), result
        inner = served.minted()
        assert inner, "the tool's calls carry a principal"
        calls = [c for c in inner if c.via == "mcp"]
        assert calls, inner
        want_sub = "token:ci" if served.fronted else "token:bare"
        for claims in calls:
            assert claims.sub == want_sub
            assert claims.scp == {"read"}, "read tier: operate is dropped"
            assert claims.aud == served.aud
            assert claims.exp - claims.iat == 60
        if served.fronted:
            assert {c.sid for c in calls} == {"s-caller"}
            assert {(c.kind, c.nm, c.cip, c.sch) for c in calls} == {
                ("agent", "CI", "192.0.2.7", "https")
            }
        served.spy.inner.clear()
        served.call("operate", "status", scp={"read", "operate"})
        assert {c.scp for c in served.minted() if c.via == "mcp"} == {
            frozenset({"read", "operate"})
        }

    def test_the_runner_s_own_reads_are_its_own(self, served):
        """Listing the tools is the runner's read, not the caller's: no `via`, only read."""
        assert "status" in served.listed("operate", scp={"read", "operate"})
        own = [c for c in served.minted() if c.via != "mcp"]
        assert own and {(c.sub, c.kind, c.scp) for c in own} == {
            ("runner:mcp", "service", frozenset({"read"}))
        }

    def test_no_request_goes_without_a_principal(self, served):
        served.call("operate", "status", scp={"read", "operate"})
        assert served.spy.inner
        assert all(token is not None for _, token in served.spy.inner), served.spy.inner

    def test_read_caller_cannot_reach_operate_route(self, served, extra_tools, rig):
        """F1, merge requirement 7: a read-tier tool that acts is refused at the door."""
        refused = served.call("read", "sneak", scp={"read", "operate"})
        assert refused.get("isError"), refused
        assert "operate" in refused["content"][0]["text"]
        assert rig.devices["heaters"].duty.value == 0.0
        allowed = served.call("operate", "sneak", scp={"read", "operate"})
        assert not allowed.get("isError"), allowed
        assert rig.devices["heaters"].duty.value == 0.5

    def test_long_tool_call_survives_60s(self, served, extra_tools, monkeypatch):
        """Each inner call is minted when it is made, so a slow tool never sends a stale one."""
        real = time.time
        _CLOCK[0] = 0.0
        monkeypatch.setattr(time, "time", lambda: real() + _CLOCK[0])
        result = served.call("read", "twice", scp={"read"})
        assert not result.get("isError"), result
        assert result["structuredContent"] == {"first": "t", "second": "t"}
        health = [c for c in served.minted() if c.via == "mcp"]
        assert len(health) == 2 and health[1].iat - health[0].iat >= 70

    def test_no_code_exec_tools_over_http(self, served):
        for tier in ("read", "author", "operate"):
            listed = served.listed(tier, scp={"read", "operate"})
            assert not {"check_driver", "search_drivers"} & listed, tier

    def test_stop_rig_tool(self, served):
        assert "stop_rig" not in served.listed("author", scp={"read", "operate"})
        result = served.call(
            "operate", "stop_rig", {"reason": "agent saw smoke"}, scp={"read", "operate"}
        )
        assert not result.get("isError"), result
        report = result["structuredContent"]
        assert report["reason"] == "agent saw smoke"
        assert report["actor"]["via"] == "mcp"
        assert report["actor"]["principal"] == ("token:ci" if served.fronted else "token:bare")


class TestSelfCall:
    def test_self_call_over_uds(self, tmp_path):
        """A fronted runner's tools dial its own socket, not a TCP port nothing listens on."""
        from flyball.runner.frontdir import FrontDir
        from flyball.runner.serving import mcp_client
        from flyball.runtime.config import RunnerConfig

        front = FrontDir(tmp_path, b"k" * 32, "a", "unix:/run/x/endpoint")
        client = mcp_client(RunnerConfig(port=8123, root_path="/r"), front)
        assert client.uds == "/run/x/endpoint"
        assert client.url == "http://localhost/r"
        bare = mcp_client(RunnerConfig(port=8123), None)
        assert bare.uds is None and bare.url == "http://127.0.0.1:8123"
        tcp = mcp_client(RunnerConfig(), FrontDir(tmp_path, b"k" * 32, "a", "tcp:127.0.0.1:8102"))
        assert tcp.uds is None and tcp.url == "http://127.0.0.1:8102"

    def test_stdio_keeps_the_code_exec_tools(self, client):
        assert {"check_driver", "search_drivers"} <= names(tools_for(client, "operate"))
        for tier in ("read", "author", "operate"):
            over_http = names(tools_for(client, tier, host_code=False))
            assert not {"check_driver", "search_drivers"} & over_http, tier

    def test_the_cap_is_the_mode_s_verbs_and_those_below(self):
        """Placeholder MCP_TIERS admit author/operate on `operate` alone; the cap keeps read."""
        from flyball.runner.serving import mcp_caps

        caps = mcp_caps()
        assert caps["read"] == {"read"}
        assert caps["author"] == {"read", "operate"}
        assert caps["operate"] == {"read", "operate"}

    def test_a_signed_rig_sends_a_fresh_principal_each_request(self):
        minted = iter(["p1", "p2"])
        rig = Client("http://x", token="bearer-ignored").acting(lambda: next(minted))
        assert rig.headers == {"X-Flyball-Principal": "p1"}
        assert rig.headers == {"X-Flyball-Principal": "p2"}


# endregion
