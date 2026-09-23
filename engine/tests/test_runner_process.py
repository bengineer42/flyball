"""`flyball-runner` as a process: where it binds, what it says, and how it stops.

Real processes on 127.0.0.1, each test on ports of its own (`free_port`, never a fixed one:
a suite running at once elsewhere would answer for this one's runner); each test tears its
runner down.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from conftest import free_port


def _runner_warnings(err: str) -> list[str]:
    """The runner's own WARNING lines, not the rig's log (the oven starts cold: a band warning)."""
    return [line for line in err.splitlines() if "WARNING" in line and "flyball.rig:" not in line]


@pytest.fixture
def port() -> int:
    """This test's runner's port."""
    return free_port()


def _lan_address() -> str | None:
    """This machine's address on its default route, if it has one (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))
        except OSError:
            return None
        address = s.getsockname()[0]
    return None if address.startswith("127.") else address


def _get(url: str, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _wait_up(proc: subprocess.Popen, port: int, deadline_s: float = 30.0) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise AssertionError(f"the runner exited {proc.returncode} before serving")
        try:
            _get(f"http://127.0.0.1:{port}/api/auth")
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("the runner never answered")


@contextmanager
def runner(cwd: Path, *argv: str, env: dict[str, str] | None = None) -> Iterator[subprocess.Popen]:
    environment = {
        k: v
        for k, v in os.environ.items()
        if k not in ("FLYBALL_PASSWORD", "FLYBALL_TOKEN", "FLYBALL_INSECURE_OPEN")
    }
    environment.update(env or {})
    proc = subprocess.Popen(
        [sys.executable, "-m", "flyball.runner", *argv],
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def test_an_open_runner_asked_for_the_network_runs_on_loopback(tmp_path, port):
    argv = ["--host", "0.0.0.0", "--port", str(port), "--store", str(tmp_path / "s.sqlite")]
    with runner(tmp_path, *argv) as proc:
        _wait_up(proc, port)
        health = _get(f"http://127.0.0.1:{port}/api/health")
        assert health["exposure"]["restricted"] and health["exposure"]["host"] == "127.0.0.1"
        auth = _get(f"http://127.0.0.1:{port}/api/auth")
        assert auth["exposure"]["requested"] == "0.0.0.0"
        lan = _lan_address()
        if lan is not None:  # not reachable on the machine's own network address
            with pytest.raises(OSError):
                _get(f"http://{lan}:{port}/api/auth", timeout=0.5)
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=15)
    assert proc.returncode == 0
    warnings = _runner_warnings(err)
    assert len(warnings) == 1 and "127.0.0.1" in warnings[0] and "--insecure-open" in warnings[0]


def test_the_environment_opts_in_for_one_run(tmp_path, port):
    argv = ["--host", "0.0.0.0", "--port", str(port), "--store", str(tmp_path / "s.sqlite")]
    with runner(tmp_path, *argv, env={"FLYBALL_INSECURE_OPEN": "1"}) as proc:
        _wait_up(proc, port)
        exposure = _get(f"http://127.0.0.1:{port}/api/auth")["exposure"]
        assert exposure["open_network"] and exposure["host"] == "0.0.0.0"
        # Asked for by a network name, it answers: the loopback-Host rule is lifted.
        lan = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/health", headers={"Host": f"192.0.2.7:{port}"}
        )
        with urllib.request.urlopen(lan, timeout=1.0) as r:
            assert r.status == 200


EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


def _open_sessions(store: Path) -> list[int]:
    from flyball.record.sqlite import SqliteStore

    s = SqliteStore(store)
    try:
        return [row.id for row in s.sessions() if row.open]
    finally:
        s.close()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_stop_signal_runs_the_cleanup(tmp_path, sig, port):
    # flyballd and systemd stop with SIGTERM: the rig must be stopped and the session
    # closed as on Ctrl-C, not left for the next start to find open.
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--port", str(port), "--store", str(store), "--record"]
    with runner(tmp_path, *argv) as proc:
        _wait_up(proc, port)
        proc.send_signal(sig)
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err[-2000:]
    assert _open_sessions(store) == [], "the recording session was left open"
    assert "Traceback" not in err


def test_sighup_is_ignored(tmp_path, port):
    # D-038: a dropped terminal (SIGHUP) never stops a runner -- unlike SIGTERM/SIGINT
    # above, the process must still be alive and serving afterwards.
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--port", str(port), "--store", str(store), "--record"]
    with runner(tmp_path, *argv) as proc:
        _wait_up(proc, port)
        proc.send_signal(signal.SIGHUP)
        time.sleep(0.5)
        assert proc.poll() is None, "SIGHUP stopped the runner"
        assert _get(f"http://127.0.0.1:{port}/api/health")["rig"] == "oven"
        assert _open_sessions(store) != [], "SIGHUP closed the recording session"
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err[-2000:]
    assert "terminal hung up" in err


def test_a_second_runner_for_the_same_rig_leaves_the_live_one_alone(tmp_path, port):
    # Two runners on one rig would drive the same hardware; the second used to close the
    # live runner's recording session and start the rig before its port bind failed.
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--port", str(port), "--store", str(store), "--record"]
    with runner(tmp_path, *argv) as live:
        _wait_up(live, port)
        before = _open_sessions(store)
        assert len(before) == 1
        second = [str(EXAMPLES / "oven.yaml"), "--port", str(free_port()), "--store", str(store)]
        with runner(tmp_path, *second, "--record") as late:
            _, err = late.communicate(timeout=20)
        assert late.returncode == 3, err[-2000:]
        assert str(live.pid) in err and "already" in err
        assert "rig version" not in err and "recording to" not in err, "nothing was started"
        assert _open_sessions(store) == before, "the live runner's session was touched"
        assert live.poll() is None
        assert _get(f"http://127.0.0.1:{port}/api/health")["recording"] is True


BOOM = """
from flyball.foundation.device import DriverConfig, Readout, Readable
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius


class Boom(Readable):
    temperature = Readout("temperature", "Temperature", Quantity("temperature", Celsius))

    def read(self, time_ns, node=None):
        yield self.sample(time_ns, temperature=20.0)


class BoomConfig(DriverConfig[Boom], type="test_boom"):
    def build(self, name, label=None):
        raise OSError("no such device: /dev/i2c-9")
"""


def test_a_rig_that_fails_to_build_exits_once_with_a_message(tmp_path, port):
    # Under flyballd a traceback exit is a crash, restarted forever; a config that
    # cannot be built is the user's to fix, like one that does not validate.
    (tmp_path / "drivers").mkdir()
    (tmp_path / "drivers" / "boom.py").write_text(BOOM)
    (tmp_path / "rig.yaml").write_text("name: boom\ndevices:\n  probe: {driver: test_boom}\n")
    with runner(tmp_path, "rig.yaml", "--port", str(port)) as proc:
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 2, err[-2000:]
    assert "Traceback" not in err
    lines = [line for line in err.splitlines() if line.startswith("flyball-runner:")]
    assert len(lines) == 1 and "rig.yaml" in lines[0] and "/dev/i2c-9" in lines[0], err


def test_a_port_it_cannot_listen_on_is_not_a_busy_rig(tmp_path, port):
    # uvicorn exits 3 when it cannot start, and 3 is flyball's "rig busy": under flyballd a
    # busy rig is never restarted. A runner that could not serve says so, with 5.
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", port))
        taken.listen()
        argv = [
            str(EXAMPLES / "oven.yaml"),
            "--port",
            str(port),
            "--store",
            str(tmp_path / "s.sqlite"),
        ]
        with runner(tmp_path, *argv) as proc:
            _, err = proc.communicate(timeout=30)
    assert proc.returncode == 5, err[-2000:]
    lines = [line for line in err.splitlines() if line.startswith("flyball-runner:")]
    assert len(lines) == 1 and "could not serve" in lines[0] and "busy" not in lines[0], err


def test_every_log_line_has_a_timestamp(tmp_path, port):
    # Under flyballd stdout and stderr are a log file: an untimed line matches nothing.
    import re

    argv = ["--port", str(port), "--store", str(tmp_path / "s.sqlite")]
    with runner(tmp_path, *argv) as proc:
        _wait_up(proc, port)  # its requests are access-log lines
        proc.send_signal(signal.SIGINT)
        out, err = proc.communicate(timeout=15)
    lines = [line for line in (out + err).splitlines() if line.strip()]
    stamp = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4} ")
    assert any("GET /api/auth" in line for line in lines), "no access log to check"
    assert any("flyball.runner" in line for line in lines), "no runner log to check"
    assert [line for line in lines if not stamp.match(line)] == []


# region Fronted: --front-dir, over a real unix socket

KEY_HEX = "0a1b2c3d" * 8
AUD = "run-5e5e5e5e"


@pytest.fixture
def front_dir() -> Iterator[Callable[..., Path]]:
    """Makes front-dirs as a front writes them, short enough for a socket path; removed after."""
    import shutil
    import tempfile

    made: list[Path] = []

    def make(key_hex: str | None = KEY_HEX) -> Path:
        folder = Path(tempfile.mkdtemp(prefix="fb-"))  # 0700; tmp_path is too long for a socket
        made.append(folder)
        files = {"key": key_hex, "aud": AUD, "endpoint": f"unix:{folder}/sock"}
        for name, text in files.items():
            if text is not None:
                (folder / name).write_text(text + "\n")
                os.chmod(folder / name, 0o600)
        return folder

    yield make
    for folder in made:
        shutil.rmtree(folder, ignore_errors=True)


def _principal(key_hex: str = KEY_HEX, scp: tuple[str, ...] = ("operate", "read")) -> dict:
    from flyball.interfaces.server.principal import Claims, mint

    now = int(time.time())
    claims = Claims("local:admin", "s-1", frozenset(scp), "human", AUD, "", "http", now, now + 60)
    return {"X-Flyball-Principal": mint(bytes.fromhex(key_hex), claims)}


def _uds(folder: Path):
    import httpx

    return httpx.Client(
        transport=httpx.HTTPTransport(uds=str(folder / "sock")), base_url="http://localhost"
    )


def _wait_fronted(proc: subprocess.Popen, folder: Path, key_hex: str = KEY_HEX) -> dict:
    import httpx

    end = time.monotonic() + 30
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise AssertionError(f"the runner exited {proc.returncode} before serving")
        try:
            with _uds(folder) as c:
                answer = c.get("/api/auth/front", headers=_principal(key_hex))
            if answer.status_code == 200:
                return answer.json()
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise AssertionError("the runner never answered on its socket")


def test_a_front_dir_without_a_key_exits_4_before_the_lock(tmp_path, front_dir):
    folder = front_dir(key_hex=None)
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--store", str(store), "--front-dir", str(folder)]
    with runner(tmp_path, *argv) as proc:
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 4, err[-2000:]
    assert "--front-dir" in err and "key" in err and "Traceback" not in err
    assert not (tmp_path / "s.sqlite.lock").exists(), "exited before taking the rig's lock"
    assert not store.exists(), "nor touched the store"


def test_a_symlinked_runner_lock_exits_4(tmp_path, front_dir):
    folder = front_dir()
    (folder / "runner.lock").symlink_to(tmp_path / "elsewhere")
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--store", str(store), "--front-dir", str(folder)]
    with runner(tmp_path, *argv) as proc:
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 4, err[-2000:]
    assert "runner.lock" in err and "Traceback" not in err
    assert not (tmp_path / "elsewhere").exists(), "the symlink's target was not touched"
    assert not store.exists()


def test_an_endpoint_outside_the_front_dir_exits_4(tmp_path, front_dir):
    folder = front_dir()
    elsewhere = Path(tempfile.mkdtemp(prefix="fb-"))
    try:
        (folder / "endpoint").write_text(f"unix:{elsewhere}/sock\n")
        store = tmp_path / "s.sqlite"
        argv = [str(EXAMPLES / "oven.yaml"), "--store", str(store), "--front-dir", str(folder)]
        with runner(tmp_path, *argv) as proc:
            _, err = proc.communicate(timeout=20)
        assert proc.returncode == 4, err[-2000:]
        assert "endpoint" in err and "Traceback" not in err
        assert not (elsewhere / "sock").exists(), "nothing bound outside the front-dir"
        assert not store.exists()
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)


def test_a_socket_it_cannot_bind_exits_5(tmp_path, front_dir):
    folder = front_dir()
    (folder / "sock").mkdir()  # something that is not a socket holds the path
    argv = [
        str(EXAMPLES / "oven.yaml"),
        "--store",
        str(tmp_path / "s.sqlite"),
        "--front-dir",
        str(folder),
    ]
    with runner(tmp_path, *argv) as proc:
        _, err = proc.communicate(timeout=30)
    assert proc.returncode == 5, err[-2000:]
    assert "could not serve" in err and "Traceback" not in err


def test_a_fronted_runner_takes_only_the_principal(tmp_path, front_dir):
    import fcntl

    folder = front_dir()
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--store", str(store), "--front-dir", str(folder)]
    env = {"FLYBALL_TOKEN": "s3cret", "FLYBALL_ANONYMOUS": "read"}
    with runner(tmp_path, *argv, env=env) as proc:
        hello = _wait_fronted(proc, folder)
        assert hello["protocol"] == 1 and hello["aud"] == AUD and hello["pid"] == proc.pid
        with _uds(folder) as c:
            unsigned = c.get("/api/auth/front")
            assert unsigned.status_code == 401
            assert unsigned.headers["x-flyball-principal-error"] == "format"
            forged = c.get("/api/health", headers=_principal("ff" * 32))
            assert forged.status_code == 401
            assert forged.headers["x-flyball-principal-error"] == "mac"
            assert (
                c.get("/api/health", headers={"Authorization": "Bearer s3cret"}).status_code == 401
            )
            assert c.get("/api/health").status_code == 401, "anonymous read ignored"
            assert c.get("/api/health", headers=_principal()).status_code == 200
        with open(folder / "runner.lock") as held, pytest.raises(BlockingIOError):
            fcntl.flock(held, fcntl.LOCK_SH | fcntl.LOCK_NB)
        assert (folder / "runner.lock").read_text() == f"pid {proc.pid} rig oven\n"
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err[-2000:]
    warnings = _runner_warnings(err)
    assert len(warnings) == 1 and "--front-dir" in warnings[0], warnings
    assert "link?n=" not in err


def test_the_fronted_socket_is_owner_only(tmp_path, front_dir):
    """The socket is 0600 (uvicorn's own is 0666): the front-dir's 0700 is not the only guard."""
    import stat

    folder = front_dir()
    argv = [str(EXAMPLES / "oven.yaml"), "--store", str(tmp_path / "s.sqlite")]
    with runner(tmp_path, *argv, "--front-dir", str(folder)) as proc:
        _wait_fronted(proc, folder)
        info = os.lstat(folder / "sock")
        assert stat.S_ISSOCK(info.st_mode)
        assert oct(stat.S_IMODE(info.st_mode)) == "0o600"
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err[-2000:]


def test_a_restart_keeps_the_runner_fronted(tmp_path, front_dir, port):
    """`os.execv` re-runs the same argv: still on the socket, still the principal only."""
    folder = front_dir()
    store = tmp_path / "s.sqlite"
    argv = [
        str(EXAMPLES / "oven.yaml"),
        "--store",
        str(store),
        "--front-dir",
        str(folder),
        "--allow-shutdown",
    ]
    with runner(tmp_path, *argv) as proc:
        _wait_fronted(proc, folder)
        fresh = "5a" * 32  # the front may write a new key; the restart reads it
        (folder / "key").write_text(fresh + "\n")
        with _uds(folder) as c:
            asked = c.post("/api/runner/restart", headers=_principal())
            assert asked.status_code == 202, asked.text
        time.sleep(0.5)
        hello = _wait_fronted(proc, folder, fresh)
        assert hello["pid"] == proc.pid, "execv: the same process"
        with _uds(folder) as c:
            assert c.get("/api/health").status_code == 401
            assert c.get("/api/health", headers=_principal()).status_code == 401, "the old key"
            assert c.get("/api/health", headers=_principal(fresh)).status_code == 200
        with pytest.raises(OSError):
            _get(f"http://127.0.0.1:{port}/api/auth", timeout=0.5)  # no TCP port


# endregion

# region Bare: removed flags, the token link


@pytest.mark.parametrize("how", ["flag", "env"])
def test_a_removed_password_warns_and_serves_loopback(tmp_path, how, port):
    """D-028: an old unit file with --password still starts; the password opens nothing."""
    argv = ["--host", "0.0.0.0", "--port", str(port), "--store", str(tmp_path / "s.sqlite")]
    env = {"FLYBALL_PASSWORD": "hunter2"} if how == "env" else {}
    if how == "flag":
        argv += ["--password", "hunter2"]
    with runner(tmp_path, *argv, env=env) as proc:
        _wait_up(proc, port)
        exposure = _get(f"http://127.0.0.1:{port}/api/health")["exposure"]
        assert exposure["open"] and exposure["host"] == "127.0.0.1" and exposure["notes"]
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=15)
    assert proc.returncode == 0
    warnings = _runner_warnings(err)
    assert len(warnings) == 1 and "removed and ignored" in warnings[0], warnings


def test_the_printed_link_signs_in_once(tmp_path, port):
    import http.client

    argv = ["--port", str(port), "--store", str(tmp_path / "s.sqlite")]
    with runner(tmp_path, *argv, env={"FLYBALL_TOKEN": "s3cret"}) as proc:
        assert proc.stderr is not None
        link = ""
        end = time.monotonic() + 30
        while not link and time.monotonic() < end:
            line = proc.stderr.readline()
            if "link?n=" in line:
                link = line.split("once, within 10 minutes: ")[1].strip()
        assert link.startswith(f"http://127.0.0.1:{port}/api/auth/link?n="), link
        _wait_up(proc, port)
        path = link.removeprefix(f"http://127.0.0.1:{port}")
        for expected in (302, 401):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", path)
            answer = conn.getresponse()
            assert answer.status == expected
            if expected == 302:
                assert answer.getheader("Location") == "/"
                assert answer.getheader("Set-Cookie", "").startswith(f"flyball-bare-{port}=")
            conn.close()


# endregion
