"""`POST /api/rig/stop` and the `SIGUSR1` break-glass: one stop, two ways in.

The interim stop (until the signals work's lands) interrupts the program, puts every
controller in manual and writes nothing: each writable device is reported `held`.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import TestClient, free_port
from flyball.foundation.device import Access
from flyball.interfaces.server import create_app, principal, set_programmer, set_rig
from flyball.interfaces.server.deps import current_stopper, get_dialect, set_stopper
from flyball.interfaces.server.dialect import program_from_document
from flyball.model.controller import ControllerMode
from flyball.rig import Rig
from flyball.runner.stopping import Actor, InterimStopper, StopReport, install_break_glass
from flyball.runtime.config import AuthConfig, load_rig_config
from flyball.sequencing import Programmer

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"
PROGRAM = {"steps": [{"regulate": {"setpoint": 50}}, {"wait": "hold on"}]}
"""Hand the oven's controller to its law, then wait for an answer that never comes."""


# region The oven, running a program


class Writes:
    """Counts every way a value can reach `device`'s hardware."""

    def __init__(self, device: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        for name in ("apply", "commit", "write_signal"):
            original = getattr(device, name)
            monkeypatch.setattr(device, name, self._counting(name, original))

    def _counting(self, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return original(*args, **kwargs)

        return counted


@pytest.fixture
def oven() -> Iterator[tuple[Rig, Programmer]]:
    rig = load_rig_config(EXAMPLES / "oven.yaml").build(start=False)
    programmer = Programmer(rig)
    set_rig(rig)
    set_programmer(programmer)
    try:
        yield rig, programmer
    finally:
        programmer.interrupt()
        set_programmer(None)
        set_rig(None)


def _run_program(rig: Rig, programmer: Programmer) -> None:
    programmer.start(program_from_document(PROGRAM, get_dialect()))
    assert programmer.state.running and programmer.state.command == "wait"
    assert rig.controllers["heater.drive"].mode is ControllerMode.REGULATING


def _outputs(rig: Rig) -> dict[str, Any]:
    return {
        signal.address: reading.value
        for signal, reading in rig.latest.items()
        if Access.W in signal.access
    }


# endregion

# region The route


def test_interim_report(oven, monkeypatch):
    rig, programmer = oven
    _run_program(rig, programmer)
    before = _outputs(rig)
    assert before == {"heater.drive": 50.0}
    writes = Writes(rig.devices["heater"], monkeypatch)

    with TestClient(create_app()) as http:
        response = http.post("/api/rig/stop", json={"reason": "lid open"})

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["program_interrupted"] is True
    assert report["controllers_manual"] == ["heater.drive"]
    assert report["interim"] is True
    assert report["reason"] == "lid open"
    # Every writable device, and only those: the thermocouple has nothing to stop.
    assert set(report["devices"]) == {"heater"}
    assert report["devices"]["heater"]["state"] == "unchanged"
    assert "nothing written" in report["devices"]["heater"]["detail"]
    assert "safe" not in json.dumps(report).lower()
    assert report["actor"]["via"] == "http"
    assert isinstance(report["at_ns"], int) and report["at_ns"] > 0

    assert programmer.state.running is False
    assert rig.controllers["heater.drive"].mode is ControllerMode.MANUAL
    assert writes.calls == []
    assert _outputs(rig) == before


def test_a_second_stop_is_idempotent(oven, monkeypatch):
    rig, programmer = oven
    _run_program(rig, programmer)
    before = _outputs(rig)
    with TestClient(create_app()) as http:
        first = http.post("/api/rig/stop").json()
        writes = Writes(rig.devices["heater"], monkeypatch)
        second = http.post("/api/rig/stop").json()

    assert first["program_interrupted"] is True
    assert second["program_interrupted"] is False  # nothing was running the second time
    for key in ("controllers_manual", "devices", "interim"):
        assert second[key] == first[key]
    assert rig.controllers["heater.drive"].mode is ControllerMode.MANUAL
    assert writes.calls == []
    assert _outputs(rig) == before


def test_stop_takes_no_body_and_forgives_a_bad_one(oven):
    with TestClient(create_app()) as http:
        bare = http.post("/api/rig/stop")
        junk = http.post(
            "/api/rig/stop", content=b"{not json", headers={"Content-Type": "application/json"}
        )
        wrong = http.post("/api/rig/stop", json={"reason": 7})
    for response in (bare, junk, wrong):
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == ""


def test_a_long_reason_is_cut_not_refused(oven):
    with TestClient(create_app()) as http:
        response = http.post("/api/rig/stop", json={"reason": "x" * 5000})
    assert response.status_code == 200
    assert len(response.json()["reason"]) <= 500


def test_no_rig_is_503():
    assert current_stopper() is None
    with TestClient(create_app()) as http:
        response = http.post("/api/rig/stop")
    assert response.status_code == 503, response.text


class Counting:
    """A stopper that remembers who asked."""

    def __init__(self) -> None:
        self.actors: list[Actor] = []

    def stop(self, actor: Actor, reason: str) -> StopReport:
        self.actors.append(actor)
        return StopReport(
            at_ns=1,
            actor=actor,
            reason=reason,
            devices={},
            program_interrupted=False,
            controllers_manual=[],
            interim=True,
        )


@pytest.fixture
def counting() -> Iterator[Counting]:
    stopper = Counting()
    set_stopper(stopper)
    try:
        yield stopper
    finally:
        set_stopper(None)


def test_stop_needs_operate(monkeypatch, counting):
    """Stop needs OPERATE: anonymous is 401, a READ-only principal 403 naming the verb.

    Neither refusal reaches the stopper; the bearer token (OPERATE) does.
    """
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    app = create_app(AuthConfig(token="s3cret", anonymous="read"))
    with TestClient(app) as http:
        door = app.state.door
        reader = principal.mint(
            door.key,
            principal.Claims(
                sub="reader",
                sid="s1",
                scp=frozenset({"read"}),
                kind="human",
                aud=door.aud,
                cip="",
                sch="http",
                iat=int(time.time()),
                exp=int(time.time()) + 60,
            ),
        )
        assert http.get("/api/health").status_code == 200  # anonymous may read ...
        anonymous = http.post("/api/rig/stop")  # ... and may not stop
        read_only = http.post("/api/rig/stop", headers={principal.HEADER: reader})
        wrong = http.post("/api/rig/stop", headers={"Authorization": "Bearer nope"})
        assert counting.actors == []
        allowed = http.post("/api/rig/stop", headers={"Authorization": "Bearer s3cret"})
    assert anonymous.status_code == 401, anonymous.text
    assert read_only.status_code == 403, read_only.text
    assert read_only.json()["needed"] == "operate"
    assert wrong.status_code == 401, wrong.text
    assert allowed.status_code == 200, allowed.text
    (actor,) = counting.actors
    assert actor.via == "http" and actor.kind == "service" and actor.sub == "token:bare"


def test_stop_not_rate_limited(counting):
    with TestClient(create_app()) as http:
        start = time.monotonic()
        codes = [http.post("/api/rig/stop").status_code for _ in range(50)]
        took = time.monotonic() - start
    assert codes == [200] * 50
    assert took < 1.0, took
    assert len(counting.actors) == 50


def test_revocation_changes_nothing(oven, monkeypatch):
    """A credential that stops working -- here, one presented wrong -- stops nothing.

    The runner-side half of merge requirement 25: its refusal is a refusal, never a
    stop. Real revocation (a named token, a session) is the front's, A3/B1.
    """
    rig, programmer = oven
    monkeypatch.delenv("FLYBALL_TOKEN", raising=False)
    app = create_app(AuthConfig(token="s3cret"))
    headers = {"Authorization": "Bearer s3cret"}
    with TestClient(app) as http:
        started = http.post("/api/programs/run", json=PROGRAM, headers=headers)
        assert started.status_code == 200, started.text
        before = _outputs(rig)
        writes = Writes(rig.devices["heater"], monkeypatch)
        revoked = {"Authorization": "Bearer revoked"}
        for method, path in (
            ("GET", "/api/programs/running"),
            ("POST", "/api/programs/interrupt"),
            ("POST", "/api/rig/stop"),
        ):
            assert http.request(method, path, headers=revoked).status_code == 401
    assert programmer.state.running is True
    assert rig.controllers["heater.drive"].mode is ControllerMode.REGULATING
    assert writes.calls == []
    assert _outputs(rig) == before


# endregion

# region The stopper itself


def test_interim_stopper_is_thread_safe(oven, monkeypatch):
    rig, programmer = oven
    _run_program(rig, programmer)
    writes = Writes(rig.devices["heater"], monkeypatch)
    stopper = InterimStopper(rig, programmer)
    actor = Actor(sub="t", sid="", kind="human", via="http")
    reports: list[StopReport] = []
    threads = [
        threading.Thread(target=lambda: reports.append(stopper.stop(actor, "t"))) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(reports) == 8
    assert sum(r.program_interrupted for r in reports) == 1
    assert all(r.devices == {"heater": reports[0].devices["heater"]} for r in reports)
    assert rig.controllers["heater.drive"].mode is ControllerMode.MANUAL
    assert writes.calls == []


def test_a_failing_controller_is_reported_not_raised(oven, monkeypatch):
    rig, programmer = oven
    _run_program(rig, programmer)
    controller = rig.controllers["heater.drive"]

    def broken() -> None:
        raise RuntimeError("stuck")

    monkeypatch.setattr(controller, "manual", broken)
    report = InterimStopper(rig, programmer).stop(Actor("t", "", "human", "http"), "")
    assert report.controllers_manual == []
    assert report.devices["heater"]["state"] == "failed"
    assert "stuck" in report.devices["heater"]["detail"]


def test_no_stopper_until_a_rig_is_set(oven):
    rig, programmer = oven
    assert isinstance(current_stopper(), InterimStopper)
    set_rig(None)
    assert current_stopper() is None
    set_rig(rig)

    fake = Counting()
    set_stopper(fake)
    try:
        assert current_stopper() is fake
    finally:
        set_stopper(None)


def test_actor_and_report_are_frozen():
    actor = Actor(sub="local:signal", sid="", kind="human", via="signal")
    report = StopReport(
        at_ns=1,
        actor=actor,
        reason="SIGUSR1",
        devices={"pump": {"state": "unchanged", "detail": ""}},
        program_interrupted=True,
        controllers_manual=["pump.flow"],
        interim=True,
    )
    with pytest.raises(AttributeError):
        report.reason = "x"  # type: ignore[misc]
    assert actor.detail == ""


def test_break_glass_off_the_main_thread_is_a_no_op():
    called: list[object] = []
    before = signal.getsignal(signal.SIGUSR1)
    thread = threading.Thread(target=lambda: install_break_glass(lambda: called.append(1)))  # type: ignore[arg-type,return-value]
    thread.start()
    thread.join()
    assert called == []
    assert signal.getsignal(signal.SIGUSR1) is before


# endregion

# region The break-glass, in a real runner


def _call(port: int, method: str, path: str, body: Any = None) -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as r:
        return json.load(r)


def _until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("never happened")


class Lines:
    """A process's stderr, read on a thread so the pipe never fills."""

    def __init__(self, stream: Any) -> None:
        self.lines: list[str] = []
        self._thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self._thread.start()

    def _read(self, stream: Any) -> None:
        for line in stream:
            self.lines.append(line)

    def matching(self, text: str) -> list[str]:
        return [line for line in list(self.lines) if text in line]


def test_sigusr1_stops_without_exit(tmp_path):
    port = free_port()
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("FLYBALL_PASSWORD", "FLYBALL_TOKEN", "FLYBALL_INSECURE_OPEN")
    }
    argv = [str(EXAMPLES / "oven.yaml"), "--port", str(port), "--store", str(tmp_path / "s.sqlite")]
    proc = subprocess.Popen(
        [sys.executable, "-m", "flyball.runner", *argv],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stderr is not None
    err = Lines(proc.stderr)
    try:

        def up() -> bool:
            assert proc.poll() is None, "".join(err.lines)
            try:
                _call(port, "GET", "/api/auth")
            except OSError:
                return False
            return True

        _until(up, 30)
        running = _call(port, "POST", "/api/programs/run", PROGRAM)
        assert running["running"] is True and running["command"] == "wait"
        controller = _call(port, "GET", "/api/controllers/heater.drive")
        assert controller["mode"] == "regulating"
        demand = controller["output"]

        proc.send_signal(signal.SIGUSR1)
        _until(lambda: _call(port, "GET", "/api/programs/running")["running"] is False)
        _until(lambda: bool(err.matching("stop report")))
        after = _call(port, "GET", "/api/controllers/heater.drive")
        assert after["mode"] == "manual"
        assert after["output"] == demand  # held, not driven
        (line,) = err.matching("stop report")
        report = json.loads(line.split("stop report: ", 1)[1])
        assert report["actor"]["via"] == "signal" and report["actor"]["sub"] == "local:signal"
        assert report["reason"] == "SIGUSR1"
        assert report["program_interrupted"] is True
        assert report["controllers_manual"] == ["heater.drive"]
        assert report["devices"] == {"heater": report["devices"]["heater"]}
        assert report["devices"]["heater"]["state"] == "unchanged"

        # A second signal: the same stop again, nothing to interrupt, still alive.
        proc.send_signal(signal.SIGUSR1)
        _until(lambda: len(err.matching("stop report")) == 2)
        second = json.loads(err.matching("stop report")[1].split("stop report: ", 1)[1])
        assert second["program_interrupted"] is False
        assert second["controllers_manual"] == ["heater.drive"]
        assert proc.poll() is None
        assert _call(port, "GET", "/api/health") is not None
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    assert proc.returncode == 0, "".join(err.lines)


def test_sigusr1_while_starting_does_not_end_the_runner(tmp_path):
    """A stop signalled while the rig is still being built is survived, not fatal.

    `flyball stop RIG-FILE` / `--front-dir` signal the pid in the lock file, which the
    runner writes long before the rig is up (18-22 s on a Pi): SIGUSR1's default action
    would end it there, and `flyball run` would start it again -- a stop turned restart.
    Sent the moment `<store>.lock` names the runner, the signal finds no rig to stop yet.
    """
    port = free_port()
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("FLYBALL_PASSWORD", "FLYBALL_TOKEN", "FLYBALL_INSECURE_OPEN")
    }
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--port", str(port), "--store", str(store)]
    proc = subprocess.Popen(
        [sys.executable, "-m", "flyball.runner", *argv],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stderr is not None
    err = Lines(proc.stderr)
    lock = tmp_path / "s.sqlite.lock"
    try:

        def named() -> bool:
            assert proc.poll() is None, "".join(err.lines)
            return lock.exists() and lock.read_text().startswith(f"pid {proc.pid}")

        _until(named, 30)
        proc.send_signal(signal.SIGUSR1)

        def up() -> bool:
            assert proc.poll() is None, (
                f"SIGUSR1 while starting ended the runner ({proc.returncode})"
            )
            try:
                _call(port, "GET", "/api/auth")
            except OSError:
                return False
            return True

        _until(up, 30)
        early = err.matching("SIGUSR1: no rig attached yet")
        late = err.matching("stop report")
        assert early or late, "".join(err.lines)
        print("the signal found:", (early or late)[0].strip())
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    assert proc.returncode == 0, "".join(err.lines)


# endregion
