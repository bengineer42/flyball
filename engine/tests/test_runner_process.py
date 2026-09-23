"""`flyball-runner` as a process: where it binds, what it says, and how it stops.

Real processes on 127.0.0.1, ports 18355-18359 only; each test tears its runner down.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

PORT = 18355


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


def test_an_open_runner_asked_for_the_network_runs_on_loopback(tmp_path):
    argv = ["--host", "0.0.0.0", "--port", str(PORT), "--store", str(tmp_path / "s.sqlite")]
    with runner(tmp_path, *argv) as proc:
        _wait_up(proc, PORT)
        health = _get(f"http://127.0.0.1:{PORT}/api/health")
        assert health["exposure"]["restricted"] and health["exposure"]["host"] == "127.0.0.1"
        auth = _get(f"http://127.0.0.1:{PORT}/api/auth")
        assert auth["exposure"]["requested"] == "0.0.0.0"
        lan = _lan_address()
        if lan is not None:  # not reachable on the machine's own network address
            with pytest.raises(OSError):
                _get(f"http://{lan}:{PORT}/api/auth", timeout=0.5)
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=15)
    assert proc.returncode == 0
    warnings = [line for line in err.splitlines() if "WARNING" in line]
    assert len(warnings) == 1 and "127.0.0.1" in warnings[0] and "--insecure-open" in warnings[0]


def test_the_environment_opts_in_for_one_run(tmp_path):
    argv = ["--host", "0.0.0.0", "--port", str(PORT), "--store", str(tmp_path / "s.sqlite")]
    with runner(tmp_path, *argv, env={"FLYBALL_INSECURE_OPEN": "1"}) as proc:
        _wait_up(proc, PORT)
        exposure = _get(f"http://127.0.0.1:{PORT}/api/auth")["exposure"]
        assert exposure["open_network"] and exposure["host"] == "0.0.0.0"


EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


def _open_sessions(store: Path) -> list[int]:
    from flyball.record.sqlite import SqliteStore

    s = SqliteStore(store)
    try:
        return [row.id for row in s.sessions() if row.open]
    finally:
        s.close()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_stop_signal_runs_the_cleanup(tmp_path, sig):
    # flyballd and systemd stop with SIGTERM: the rig must be stopped and the session
    # closed as on Ctrl-C, not left for the next start to find open.
    store = tmp_path / "s.sqlite"
    argv = [str(EXAMPLES / "oven.yaml"), "--port", str(PORT), "--store", str(store), "--record"]
    with runner(tmp_path, *argv) as proc:
        _wait_up(proc, PORT)
        proc.send_signal(sig)
        _, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err[-2000:]
    assert _open_sessions(store) == [], "the recording session was left open"
    assert "Traceback" not in err
