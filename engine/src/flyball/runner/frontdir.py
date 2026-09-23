"""The front-dir: what a front hands the runner it starts (`flyball-runner --front-dir DIR`).

The front makes one directory per runner, mode 0700, and writes three files into it, each
0600: `key` (64 lower-case hex characters, the principal's HMAC key, fresh at every
spawn), `aud` (the audience the front assigned) and `endpoint` (`unix:<abs path>`, or
`tcp:<loopback>:<port>`). The runner binds `endpoint` and nothing else, takes a principal
only if it verifies with `key` for `aud`, and holds `runner.lock` there for its life.

Anything unsafe or missing means the front and the runner disagree about how they talk, not
a setting a user got wrong: the runner exits 4 (`FRONT_DIR`) before it takes the rig's lock
or touches hardware, and the front writes the directory again.
"""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from flyball.interfaces.server.principal import read_key_file

MAX_SOCKET_PATH = 100
"""The longest unix socket path accepted: `sun_path` is 108 bytes on Linux, 104 on macOS."""

_AUD = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\n?\Z")


class Unusable(Exception):
    """The front-dir is unsafe, incomplete or malformed."""


@dataclass(frozen=True)
class FrontDir:
    path: Path
    key: bytes
    aud: str
    endpoint: str
    """As written: `unix:/abs/path` or `tcp:127.0.0.1:8102`."""

    @property
    def network(self) -> str:
        return self.endpoint.split(":", 1)[0]

    @property
    def address(self) -> str:
        return self.endpoint.split(":", 1)[1]

    @property
    def host(self) -> str:
        return self.address.rsplit(":", 1)[0].strip("[]")

    @property
    def port(self) -> int:
        return int(self.address.rsplit(":", 1)[1])


def _private(path: Path, what: str, kind: int) -> None:
    """`path` is this user's, not a symlink, of the right kind, closed to group and others."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise Unusable(
            f"{what}: no such {'directory' if kind == stat.S_IFDIR else 'file'} {path}"
        ) from None
    except OSError as e:
        raise Unusable(f"{what}: {e}") from None
    if stat.S_ISLNK(info.st_mode):
        raise Unusable(f"{what}: {path} is a symlink")
    if stat.S_IFMT(info.st_mode) != kind:
        raise Unusable(f"{what}: {path} is not a {'directory' if kind == stat.S_IFDIR else 'file'}")
    if info.st_uid != os.geteuid():
        raise Unusable(f"{what}: {path} belongs to uid {info.st_uid}, not this runner's")
    if info.st_mode & 0o077:
        want = "0700" if kind == stat.S_IFDIR else "0600"
        raise Unusable(f"{what}: {path} is mode {oct(stat.S_IMODE(info.st_mode))}, not {want}")


def parse_endpoint(text: str) -> str:
    """`text` if it is an endpoint a runner may bind: an absolute socket path, or loopback TCP."""
    network, _, address = text.partition(":")
    if network == "unix":
        if not address.startswith("/"):
            raise Unusable(f"endpoint: {text!r} is not an absolute socket path")
        if len(address.encode()) > MAX_SOCKET_PATH:
            raise Unusable(f"endpoint: the socket path is over {MAX_SOCKET_PATH} bytes")
        return text
    if network == "tcp":
        host, _, port = address.rpartition(":")
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host.strip("[]")).is_loopback
            number = int(port)
        except ValueError:
            raise Unusable(f"endpoint: {text!r} is not tcp:<loopback address>:<port>") from None
        if not loopback or not 0 < number < 65536:
            raise Unusable(f"endpoint: {text!r} is not tcp:<loopback address>:<port>")
        return text
    raise Unusable(f"endpoint: {text!r} is neither unix:<path> nor tcp:<host>:<port>")


def read(path: Path) -> FrontDir:
    """The front-dir at `path`, checked; raises [Unusable][flyball.runner.frontdir.Unusable]."""
    _private(path, "front-dir", stat.S_IFDIR)
    for name in ("key", "aud", "endpoint"):
        _private(path / name, name, stat.S_IFREG)
    try:
        key = read_key_file(path / "key")
    except (OSError, ValueError) as e:
        raise Unusable(f"key: {e}") from None
    try:
        aud = (path / "aud").read_text(encoding="ascii")
        endpoint = (path / "endpoint").read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as e:
        raise Unusable(f"aud or endpoint: {e}") from None
    if not _AUD.match(aud):
        raise Unusable(f"aud: {aud!r} is not an audience")
    line = endpoint.removesuffix("\n")
    if "\n" in line:
        raise Unusable(f"endpoint: {endpoint!r} is not one line")
    return FrontDir(path, key, aud.removesuffix("\n"), parse_endpoint(line))
