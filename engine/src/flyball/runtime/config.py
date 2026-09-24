"""A rig as a file: links, devices and controllers, built in that order.

A tree of typed configs. Links are declared once and named by the devices
that use them; a device entry is flyball's envelope around the driver's own
config, keyed by name; a controller is keyed by the address of
the demand it drives, its output, and names its `measured` signal. Formats are
[flyball.foundation.files][]'s business; which driver and link kinds exist is
[flyball.model.catalog.Catalogs][]'s, read here via
[get_catalog][flyball.model.catalog.get_catalog] where a function has no way
to take one as a parameter (a pydantic classmethod, a validator), and as an
explicit `catalogs` argument (default: the same) where it does.

Every type resolves to a real constructor, so the file validates against the
models the code is built from, including configs another package registered
through the `flyball.configs` entry point. The same file with `fake_text`
and `fake_registers` links runs without hardware.

A file may start from a **board**: a profile, an installed package's data or
kept outside any package, that declares the links a machine has and names
its pins. `board = "rpi5"` is looked up on the board path; the file's own
`links` are added to the profile's, and a device's `pin = "GPIO18"` becomes
the link and line the profile says.

The `readers`, `actuators` and `loops` sections of the legacy model no
longer parse; devices and controllers replace them (`book/src/7-reference/rig-file.md`).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidatorFunctionWrapHandler,
    create_model,
    field_validator,
    model_validator,
)
from pydantic.json_schema import GenerateJsonSchema

# Nothing built into flyball core registers a type implicitly any more --
# `scpi`/`modbus` (extensions/visa, extensions/modbus), the Linux buses and
# chips, flyball-sim's sim_plant/sim_daq/sim_drive, and engine's own laws,
# feedforwards and generators (`control/configs.py`) all register through the
# `flyball.configs` entry point instead, read by `Catalogs.discover()`.
from flyball.control import (
    IMC,
    PI,
    PID,
    Affine,
    OnOff,
    OpenLoop,
    P,
    Scheduled,
    SlidingMode,
    SmithPredictor,
    Table,
)
from flyball.foundation.config import Config, discover_paths, discriminated_union
from flyball.foundation.device import RESERVED_NAMES, Device, DeviceEntry, DriverConfig, Signal
from flyball.foundation.device.entry import Reads
from flyball.foundation.errors import ConflictError, NotFoundError
from flyball.foundation.files import SUFFIXES, load_document
from flyball.foundation.keys import Keyed, check_address, check_key, check_keys
from flyball.foundation.keys import canonical as canonical_key
from flyball.foundation.schema import Titled
from flyball.foundation.time import Clock
from flyball.model.catalog import Catalogs, ensure_discovered, get_catalog
from flyball.model.controller import OnFault
from flyball.model.feedforward import Identity, NoFeedforward
from flyball.rig import Rig
from flyball.rig.polling import BACKOFF_S, FAIL_AFTER, ReadPolicy
from flyball.rig.stopping import stop_plan

log = logging.getLogger(__name__)

# `LawConfig`/`FeedforwardConfig` are static pydantic field types
# (`ControllerEntry` below), so they need every built-in law/feedforward at
# import time, not `get_catalog()` -- unlike `registered()`, which reads it
# lazily per call for devices/links (the extension point; a third-party
# driver may not be imported yet). Laws and feedforwards have no extension
# point today (nothing outside engine defines one, `control/configs.py`
# registers all of them), so importing the built-ins directly here is
# equivalent, and doesn't risk `Catalogs.discover()` re-entering this module
# through an extension's own import chain (`flyball_sim`, notably, imports
# `RigConfig` from here).
_LAWS = (OpenLoop, P, PI, PID, IMC, OnOff, SmithPredictor, Scheduled, SlidingMode)
_FEEDFORWARDS = (Identity, NoFeedforward, Affine, Table)
LawConfig = discriminated_union(
    {law.type: law for law in _LAWS}, "type", lambda law: law.config_type
)
FeedforwardConfig = discriminated_union(
    {ff.type: ff for ff in _FEEDFORWARDS}, "type", lambda ff: ff.config_type
)

Role = Literal["link", "driver"]

LEGACY_SECTIONS = ("readers", "actuators", "loops")
LEGACY_MESSAGE = (
    "readers/actuators/loops are no longer rig-file sections; devices and controllers"
    " replace them, see book/src/7-reference/rig-file.md"
)


# region Which type plays which part


def role_of(config: type[Config[Any]]) -> Role:
    """A device driver or a link: a driver subclasses `DriverConfig`, anything else is a link."""
    return "driver" if issubclass(config, DriverConfig) else "link"


def registered(role: Role, catalogs: Catalogs | None = None) -> tuple[type[Config[Any]], ...]:
    """Every typed config playing `role`, in type order.

    Args:
        role: `"driver"` or `"link"`.
        catalogs: Default: [get_catalog][flyball.model.catalog.get_catalog].
    """
    catalogs = catalogs or get_catalog()
    catalog = catalogs.devices if role == "driver" else catalogs.links
    return tuple(catalog[name] for name in sorted(catalog.names()))


# endregion
# region The models


class FreezeThen(BaseModel):
    """`on_fault: {freeze_s: <s>, then: <action>}`: frozen `freeze_s` of fault time, then act."""

    model_config = ConfigDict(extra="forbid")

    freeze_s: float = Field(
        ge=0,
        description="Seconds of fault time (accrued across flicker) to stay frozen before `then`.",
    )
    then: Literal["manual", "stop", "stop_device"]


type OnFaultEntry = Literal["freeze", "manual", "stop", "stop_device"] | FreezeThen


class ControllerEntry(BaseModel):
    """A controller and how it regulates its output; keyed by the output's address in the file."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(
        default=None,
        description="What a person reads; none: the output signal's label.",
    )
    measured: str = Field(description="The measured signal's address (a P signal).")
    law: LawConfig | None = None  # type: ignore[valid-type]
    feedforward: FeedforwardConfig | None = Field(  # type: ignore[valid-type]
        default=None,
        description="Maps the measured signal's unit to the output's; the law adds to it."
        " Omit for identity (the setpoint itself) when the units agree, else none.",
    )
    is_default: bool = False
    min_period_s: float | None = Field(
        default=None,
        gt=0,
        description="Update the law at most this often; omit to update on every reading.",
    )
    setpoint_period_s: float | None = Field(
        default=None,
        gt=0,
        description="While following a moving setpoint (a ramp, a profile), re-apply its"
        " feedforward this often between readings; the law steps only on readings. Omit for"
        " max(0.1 s, poll_s / 4) from the measured signal's poll_s.",
    )
    on_fault: OnFaultEntry = Field(
        default="freeze",
        description="What it does once its source has been faulty (stale, invalid, offline) for"
        " its wait, or at once when its law raises: freeze (default: stays frozen, resumes by"
        " itself), manual, stop (its output's stop), stop_device (its output's device's stop),"
        " or {freeze_s: <s>, then: manual|stop|stop_device}. Each but freeze latches until a"
        " person resets it; a law error takes at least manual.",
    )

    def fault_policy(self) -> OnFault:
        """`on_fault` as the controller holds it."""
        value = self.on_fault
        return OnFault.parse(value.model_dump() if isinstance(value, FreezeThen) else value)


class ClockEntry(BaseModel):
    """How the rig's time runs. Only a rig with nothing real on it may run off wall time."""

    model_config = ConfigDict(extra="forbid")

    speed: float = Field(default=1.0, gt=0, description="Rig seconds per wall second.")
    stepped: bool = Field(
        default=False, description="Time moves only when stepped; for a batch run or a test."
    )


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ns|us|ms|s|m|h|d|w)?\s*$", re.IGNORECASE)
_DURATION_NS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
    "d": 86_400_000_000_000,
    "w": 604_800_000_000_000,
}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(i?)b?\s*$", re.IGNORECASE)
_SIZE_EXPONENT = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}


def parse_duration_ns(text: str | int | float) -> int:
    """`1h`, `30m`, `90s`, `2d`, `500ms` -- or a bare number of seconds -- as nanoseconds.

    `0` (any spelling) is zero: the runner reads that as *off* or *forever*.
    Anything else raises `ValueError`.
    """
    if isinstance(text, (int, float)):
        return round(text * 1_000_000_000)
    match = _DURATION.match(text)
    if match is None:
        raise ValueError(f"{text!r} is not a duration: a number with ns, us, ms, s, m, h, d or w")
    number, unit = match.groups()
    return round(float(number) * _DURATION_NS[(unit or "s").lower()])


def parse_size_bytes(text: str | int | float) -> int:
    """`256MB`, `20GB`, `1.5GiB`, `4096` -- as bytes: decimal for `kB`..`TB`, binary with an `i`.

    `0` is zero, which the runner reads as *no cap*. Anything else raises `ValueError`.
    """
    if isinstance(text, (int, float)):
        return round(text)
    match = _SIZE.match(text)
    if match is None:
        raise ValueError(f"{text!r} is not a size: a number with kB, MB, GB, TB or KiB, MiB, ...")
    number, prefix, binary = match.groups()
    base = 1024 if binary else 1000
    return round(float(number) * base ** _SIZE_EXPONENT[prefix.lower()])


Anonymous = Literal["none", "read"]


class AuthConfig(BaseModel):
    """The `runner.auth` section: who may reach a *bare* runner, one with no front.

    A *token* is the one credential: machines send it as a bearer header, and a person
    trades it (or the one-time link the runner prints at start) for a session cookie. What a
    caller with neither may do is `anonymous`: nothing, or read. With no token the runner is
    open, and served on loopback only unless the run itself says otherwise (see
    [settle_exposure][flyball.runtime.config.settle_exposure]); that switch is never a key
    here, so no file -- nor anything it `extends` -- can open a runner.

    A runner the front started (`--front-dir`) ignores this section: the front decides who
    gets in. `password`, `session` and `secret` are removed: parsed, so an old file still
    starts, and ignored with a warning.
    """

    model_config = ConfigDict(extra="forbid")

    token: str | None = Field(
        default=None, description="Bearer token for the CLI, MCP clients, scripts and the UI."
    )
    anonymous: Anonymous = Field(
        default="none",
        description="What a caller with no session and no token may do: nothing, or read"
        " (every GET and every stream).",
    )
    password: str | None = Field(
        default=None,
        description="Removed: ignored with a warning. The bare runner has no password login;"
        " use `token`, or run it under `flyball run` (`runner.front`).",
        json_schema_extra={"deprecated": True},
    )
    session: str | None = Field(
        default=None,
        description="Removed: ignored with a warning. A token-link session lasts 12 h.",
        json_schema_extra={"deprecated": True},
    )
    secret: str | None = Field(
        default=None,
        description="Removed: ignored with a warning. Sessions are no longer signed.",
        json_schema_extra={"deprecated": True},
    )

    @property
    def enabled(self) -> bool:
        """Whether anyone is refused: a token is set."""
        return bool(self.token)

    @property
    def removed(self) -> list[str]:
        """The removed keys this section sets, each ignored."""
        return [key for key in ("password", "session", "secret") if getattr(self, key) is not None]


class TlsFiles(BaseModel):
    """`runner.front.tls`: the certificate the front serves, reloaded when renewed."""

    model_config = ConfigDict(extra="forbid")
    cert: Path
    key: Path


class CustomJwt(BaseModel):
    """`runner.front.proxy.jwt`: a signed assertion the `custom` preset verifies."""

    model_config = ConfigDict(extra="forbid")
    header: str
    jwks_url: str
    issuer: str
    audience: str
    algorithms: list[str]


class ProxyConfig(BaseModel):
    """`runner.front.proxy`: the identity layer in front of the front (`auth: proxy`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    preset: Literal[
        "tailscale", "authelia", "oauth2-proxy", "authentik", "pomerium", "cloudflare", "custom"
    ]
    from_: Literal["unix"] | list[str] | None = Field(
        default=None,
        alias="from",
        description="Whose unsigned headers are believed: `unix` (the front's socket), or"
        " addresses and CIDRs.",
    )
    secret_file: Path | None = None
    team: str | None = Field(default=None, description="cloudflare: the Access team name.")
    issuer: str | None = None
    audience: str | None = None
    grants: dict[str, list[str]] = Field(
        default_factory=dict,
        description="A grant (names pending D-034) -> subjects and `group:<id>`s.",
    )
    user_header: str | None = None
    groups_header: str | None = None
    separator: str | None = None
    jwt: CustomJwt | None = None


class TokensConfig(BaseModel):
    """`runner.front.tokens` (or flyballd.yaml's top-level `tokens:`): named-token lifetimes.

    Read and validated by the front (Go, `daemon/internal/front.ResolveLifetimes`); this side
    only shapes the block and forbids unknown keys. An unparseable, out-of-range or
    otherwise invalid value falls back to the built-in, with a warning at start -- it never
    stops the runner (D-028).
    """

    model_config = ConfigDict(extra="forbid")
    default_lifetime: str | None = Field(
        default=None,
        description="A token's lifetime when created without an explicit `expires_in`. A Go"
        " duration plus a `d` suffix for days (e.g. `90d`, `36h`). Unset: the built-in 90 days.",
    )
    max_lifetime: str | None = Field(
        default=None,
        description="The hard cap on a token's lifetime, for tokens that are neither cleartext"
        " nor kind `agent` (those keep a fixed 30-day cap, tightened further if this is"
        " smaller). May only tighten the built-in ceiling of 365 days, never loosen it."
        " Unset: the built-in 365 days.",
    )


class FrontConfig(BaseModel):
    """`runner.front`: how `flyball run`'s front serves this rig. Read by the front, never here."""

    model_config = ConfigDict(extra="forbid")
    listen: str = Field(default="127.0.0.1:8000", description="Where the front listens.")
    auth: Literal["local", "password", "proxy", "sso"] = Field(
        default="local", description="Who gets in: the shape."
    )
    url: str | None = Field(default=None, description="The external URL the rig is reached at.")
    tls: TlsFiles | None = None
    password: str | None = Field(
        default=None, description="`auth: password`: a `$scrypt$` line from `flyball password`."
    )
    anonymous: Anonymous = "none"
    proxy: ProxyConfig | None = None
    uv: bool = False
    login: str = "12h"
    trusted_proxies: list[str] = Field(default_factory=list)
    tokens: TokensConfig | None = Field(
        default=None, description="Named-token lifetime ceilings: default_lifetime, max_lifetime."
    )


def is_loopback(host: str) -> bool:
    """Whether a bind address reaches this machine only: `localhost`, `127.0.0.0/8`, `::1`.

    Anything else -- `0.0.0.0`, `::`, an empty host, a LAN address, a name that is not
    `localhost` -- may be reachable from elsewhere, and counts as not.
    """
    import ipaddress

    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


LOOPBACK = "127.0.0.1"
"""Where an open runner asked for a network address is served instead."""


@dataclass(frozen=True)
class Exposure:
    """Where the runner serves, against where it was asked to, and what to say about it."""

    requested_host: str
    """The bind address asked for (`runner.host`, `--host`)."""
    host: str
    """The bind address served on: `requested_host`, or loopback for an open runner."""
    port: int
    open: bool
    """No password and no token: whoever reaches the port may operate the rig."""
    warning: str | None = None
    """One line for stderr, or None when there is nothing to say."""
    endpoint: str | None = None
    """What the runner binds, `tcp:<host>:<port>` or `unix:<path>` (fronted)."""
    fronted: bool = False
    """Started by a front (`--front-dir`): the principal is the only credential."""
    notes: tuple[str, ...] = ()
    """Settings the runner ignores (removed keys; `runner.auth` when fronted), one line each."""

    @property
    def restricted(self) -> bool:
        """Moved to loopback because the runner is open."""
        return self.host != self.requested_host

    @property
    def open_network(self) -> bool:
        """Open, and reachable beyond this machine: opted into by the run."""
        return self.open and not is_loopback(self.host)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_host": self.requested_host,
            "host": self.host,
            "port": self.port,
            "open": self.open,
            "restricted": self.restricted,
            "open_network": self.open_network,
            "warning": self.warning,
            "endpoint": self.endpoint,
            "fronted": self.fronted,
            "notes": list(self.notes),
        }


def ignored_auth(auth: AuthConfig, fronted: bool) -> tuple[str, ...]:
    """What the runner says it ignores of `auth`: removed keys, and all of it when fronted."""
    notes = []
    for key in auth.removed:
        flag = {
            "password": " (--password, FLYBALL_PASSWORD)",
            "session": " (--session, FLYBALL_SESSION)",
        }.get(key, "")
        notes.append(
            f"runner.auth.{key}{flag} is removed and ignored: the bare runner has no password"
            " login; give it a token (runner.auth.token, --token, --token-file), or run it"
            " under `flyball run`"
        )
    if fronted and (auth.token or auth.anonymous != "none"):
        notes.append(
            "started by a front (--front-dir): runner.auth, --token and --anonymous are"
            " ignored; the front decides who gets in (runner.front)"
        )
    return tuple(notes)


def _tcp(host: str, port: int) -> str:
    return f"tcp:[{host}]:{port}" if ":" in host else f"tcp:{host}:{port}"


def settle_exposure(
    settings: RunnerConfig, insecure_open: bool = False, *, endpoint: str | None = None
) -> Exposure:
    """Where to bind: a misconfiguration removes exposure, never operation.

    Open is no token (`runner.auth.token`, `--token`, `--token-file` or `FLYBALL_TOKEN`, all
    settled into `settings` by then): anyone who reaches the port may operate the rig. On
    loopback that is only this machine. Asked for any other address, an open runner still
    starts -- a control process that will not start leaves the equipment uncontrolled --
    but binds loopback on the same port, and the warning says why and how to fix it.
    `insecure_open` (`--insecure-open`, `FLYBALL_INSECURE_OPEN=1`: per run, never a
    rig-file key) serves it where asked. A token beyond loopback over plain HTTP gets a
    warning. A removed `runner.auth` key (`password`, `session`, `secret`) counts as absent:
    a password-only runner is open, so it serves loopback.

    With `endpoint` (a fronted runner: the front-dir's `endpoint`) the runner binds that and
    nothing else; `runner.host`/`port` and `runner.auth` are ignored, and `notes` says so.
    """
    if endpoint is not None:
        notes = ignored_auth(settings.auth, fronted=True)
        return Exposure(
            endpoint,
            endpoint,
            0,
            open=False,
            warning="; ".join(notes) or None,
            endpoint=endpoint,
            fronted=True,
            notes=notes,
        )
    notes = ignored_auth(settings.auth, fronted=False)
    requested, port = settings.host, settings.port
    where = f"{requested or 'every interface'!r}"

    def exposure(host: str, open: bool, warning: str | None = None) -> Exposure:
        said = "; ".join(filter(None, (*notes, warning))) or None
        return Exposure(requested, host, port, open, said, endpoint=_tcp(host, port), notes=notes)

    if is_loopback(requested):
        return exposure(requested, not settings.auth.enabled)
    if settings.auth.enabled:
        return exposure(
            requested,
            False,
            f"serving plain HTTP on {where}: the token and session cookies cross the network"
            " unencrypted; put TLS in front (`flyball run` with runner.front.tls), or serve on"
            " 127.0.0.1",
        )
    if insecure_open:
        return exposure(
            requested,
            True,
            f"serving an OPEN runner on {where} (--insecure-open): anyone who can reach"
            " it may operate the rig",
        )
    return exposure(
        LOOPBACK,
        True,
        f"host is {where} but the runner has no token: serving on"
        f" {LOOPBACK}:{port} only, so nothing beyond this machine can reach the rig. To serve it"
        " on the network give it a token (runner.auth.token in the rig file, --token,"
        " --token-file, FLYBALL_TOKEN) or run it under `flyball run`, or, knowingly,"
        " --insecure-open or FLYBALL_INSECURE_OPEN=1",
    )


class RunnerReads(BaseModel):
    """`runner.reads`: the rig's default for when failed reads put a device offline.

    A device's own `reads:` wins key by key. `give_up_after_s` is a device's
    only: rig-wide, a device retries for ever.
    """

    model_config = ConfigDict(extra="forbid")

    fail_after: int = Field(
        default=FAIL_AFTER, description="Reads that raise in a row before a device is offline."
    )
    backoff_s: list[float] = Field(
        default_factory=lambda: list(BACKOFF_S),
        description="Seconds between retries while offline, in turn; the last repeats.",
    )

    @field_validator("fail_after", "backoff_s")
    @classmethod
    def _as_a_device_s(cls, value: Any, info: Any) -> Any:
        Reads.model_validate({info.field_name: value})  # the same rules, the same messages
        return value


class RunnerConfig(BaseModel):
    """The `runner:` section: how the process serves, not what the rig is.

    Everything here is fixed for the life of the process and says nothing
    about the equipment, so it may live in the rig file or in a file of its
    own that layers with it. A command-line flag overrides a value here.
    A path is relative to the first rig file's directory.
    """

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="Bind address; loopback unless reachable.")
    port: int = 8000
    log_level: str = "info"
    store: Path | None = Field(
        default=None, description="Sessions and versions; default <rig>.sqlite beside the file."
    )
    store_dir: Path | None = Field(
        default=None, description="Where stores live, one per rig by name: <dir>/<name>.sqlite."
    )
    programs: Path | None = Field(
        default=None, description="Program files to import; default programs/ beside the file."
    )
    tunings: Path | None = Field(
        default=None, description="Control-law configs; default tunings/ beside the file."
    )
    drivers: Path | None = Field(
        default=None, description="Driver .py files; default drivers/ beside the file."
    )
    auth: AuthConfig = Field(
        default_factory=AuthConfig,
        description="Who may reach a bare runner (no front): token, anonymous.",
    )
    front: FrontConfig | None = Field(
        default=None,
        description="How `flyball run`'s front serves the rig: listen, auth, url, tls. Read by"
        " the front only; a runner the front started ignores `host`, `port` and `auth`.",
    )
    compose: bool = Field(default=False, description="Build up a hardware rig over the API.")
    mcp: bool = Field(default=True, description="Mount the MCP servers at /mcp.")
    root_path: str | None = Field(default=None, description="Serve under this path prefix.")
    allow_save: bool = Field(
        default=False, description="Let the API write rig files: a save to a path, a sim save."
    )
    allow_shutdown: bool = Field(
        default=False, description="Let the API stop or restart the runner."
    )
    on_shutdown: Literal["stop", "keep"] = Field(
        default="stop",
        description="What the runner's shutdown does to outputs: stop (each device's resolved"
        " stop, best-effort, not latched) or keep (writes nothing: outputs stay energised with"
        " no process watching them). A device's own `on_shutdown: keep` wins over stop.",
    )
    reads: RunnerReads = Field(
        default_factory=RunnerReads,
        description="When failed reads put a device offline, and how it retries; a device's"
        " own `reads:` wins.",
    )
    keep: str = Field(
        default="1h",
        description="How much the scratch record holds while nothing is recorded, in the rig's"
        " clock (`1h`, `30m`); `0` keeps none.",
    )
    keep_size: str = Field(
        default="256MB",
        description="The most the scratch record may take on disk; the oldest goes first.",
    )
    retain: str = Field(
        default="0",
        description="Delete an unpinned session this long after it ended (`30d`); `0` keeps all.",
    )
    rotate: str = Field(
        default="0",
        description="Close a recording at this length and continue it in a new session (`24h`);"
        " `0` never rotates.",
    )
    max_store: str = Field(
        default="0",
        description="Keep the store under this size by deleting the oldest data, of any kind,"
        " never pinned (`20GB`); `0` sets no cap.",
    )
    run: dict[str, Any] = Field(
        default_factory=dict,
        description="Deprecated: `runner.front`. Accepted for one release: `serve_ui` is read"
        " as `front.listen`, `uv` as `front.uv`, with a warning.",
        json_schema_extra={"deprecated": True},
    )

    @model_validator(mode="before")
    @classmethod
    def _run_is_front(cls, data: Any) -> Any:
        # `runner.run` before `runner.front` existed: its two keys move there, unless the file
        # already says `front`, which wins.
        if not isinstance(data, dict) or not data.get("run") or "front" in data:
            return data
        run = data["run"]
        if not isinstance(run, dict):
            return data
        front = {new: run[old] for old, new in (("serve_ui", "listen"), ("uv", "uv")) if old in run}
        log.warning("runner.run is deprecated: write runner.front (serve_ui is now front.listen)")
        return {**data, "front": front}

    @field_validator("front", mode="wrap")
    @classmethod
    def _front_never_stops_the_runner(
        cls, value: Any, handler: ValidatorFunctionWrapHandler
    ) -> Any:
        # D-028: `runner.front` is the front's; a mistake in it must not stop the rig. The
        # strict check is the schema's, which `flyball rig check` enforces.
        try:
            return handler(value)
        except ValidationError as e:
            said = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()
            )
            # The keys are the caller's (`POST /api/rig/check` needs only read): escaped, so
            # a newline in one cannot start a line of its own in the log.
            said = said.encode("unicode_escape").decode("ascii")
            log.warning("runner.front is not valid and is ignored here: %s", said)
            return None

    @model_validator(mode="before")
    @classmethod
    def _token_alias(cls, data: Any) -> Any:
        # `runner.token` from before `auth:` existed, and `RunnerConfig(token=...)`: the same
        # thing as `auth.token`, so it moves there rather than failing `extra="forbid"`.
        if isinstance(data, dict) and "token" in data:
            data = dict(data)
            token = data.pop("token")
            auth = data.get("auth")
            auth = auth.model_dump() if isinstance(auth, BaseModel) else dict(auth or {})
            auth.setdefault("token", token)
            data["auth"] = auth
        return data

    @field_validator("keep", "retain", "rotate", mode="before")
    @classmethod
    def _duration(cls, value: Any) -> str:
        parse_duration_ns(value)  # a bad spelling fails here, not when the runner first sweeps
        return str(value)

    @field_validator("keep_size", "max_store", mode="before")
    @classmethod
    def _size(cls, value: Any) -> str:
        parse_size_bytes(value)
        return str(value)

    @property
    def keep_ns(self) -> int:
        return parse_duration_ns(self.keep)

    @property
    def keep_bytes(self) -> int:
        return parse_size_bytes(self.keep_size)

    @property
    def retain_ns(self) -> int:
        return parse_duration_ns(self.retain)

    @property
    def rotate_ns(self) -> int:
        return parse_duration_ns(self.rotate)

    @property
    def max_bytes(self) -> int:
        return parse_size_bytes(self.max_store)


def _canonical_link(entry: Any) -> Any:
    """A device entry whose `link` names a link in either spelling, as the canonical name."""
    if isinstance(entry, dict) and isinstance(entry.get("link"), str):
        return {**entry, "link": canonical_key(entry["link"])}
    return entry


def is_simulated(links: dict[str, Any]) -> bool:
    """Whether every link is a fake or a simulation, so time may be played with."""
    return all(
        (name := getattr(link, "type_name", None)) is not None
        and (name.startswith("sim_") or name.startswith("fake_"))
        for link in links.values()
    )


def resolve_live(path: str, root: Any) -> Any:
    """What a config field's `live` path points at in `root`, the object it is resolved against.

    The grammar: dot-separated keys walked from `root` (`output`,
    `stats.noise`, `readings.zone1.value`); a `*` segment fans out over
    every key at that level and yields a dict keyed by them
    (`outputs.*` -> `{"zone1": 603.7, ...}`, `readings.*.value`). None when
    a key is missing; a fan-out drops keys the rest of the path misses.

    Generic dict-path resolution, not simulation-specific itself; used by
    `flyball_sim.simulation.Simulation.live` to resolve a plant's `live`
    fields against its own description.
    """
    return _resolve_live(path.split(".") if path else [], root)


def _resolve_live(segments: list[str], node: Any) -> Any:
    if not segments:
        return node
    head, rest = segments[0], segments[1:]
    if not isinstance(node, dict):
        return None
    if head == "*":
        found = {key: _resolve_live(rest, value) for key, value in node.items()}
        return {key: value for key, value in found.items() if value is not None}
    return _resolve_live(rest, node.get(head))


class RigConfig(BaseModel):
    """The whole file.

    `model_validate` and `model_json_schema` on this class use
    [get_catalog][flyball.model.catalog.get_catalog] -- the types registered
    there at the time of the call -- so a config registered after import is
    as valid in a file as a built-in one.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    label: str | None = Field(
        default=None, description="What a person reads for the rig; none: its name, humanised."
    )
    board: str | None = Field(
        default=None,
        description="A board profile: a name on the board path, or a path to the file.",
    )
    recording: bool = Field(
        default=False, description="Open a recording session when the runner starts."
    )
    clock: ClockEntry | None = Field(
        default=None, description="Run the rig's time faster, or stepped; simulated rigs only."
    )
    links: dict[str, Any] = Field(default_factory=dict)
    devices: dict[str, DeviceEntry] = Field(default_factory=dict)
    controllers: dict[str, ControllerEntry] = Field(
        default_factory=dict, description="Keyed by the output signal's address."
    )
    runner: RunnerConfig | None = Field(default=None, exclude=True)
    """How the runner serves; not part of the rig, so not of its document or versions."""
    files: list[Path] = Field(default_factory=list, exclude=True)
    """The files this was loaded from, set by `load_rig_config`; not part of the document."""
    resumed: bool = Field(default=False, exclude=True)
    """Loaded from a stored rig version rather than the files (`flyball-runner --resume`)."""

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> RigConfig:  # type: ignore[override]
        if cls is RigConfig:
            return rig_model(get_catalog()).model_validate(obj, **kwargs)
        return super().model_validate(obj, **kwargs)

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        kwargs.setdefault("schema_generator", Titled)
        if cls is RigConfig:
            catalogs = get_catalog()
            schema = rig_model(catalogs).model_json_schema(**kwargs)
            devices_schema, devices_defs = _devices_schema(catalogs)
            schema.setdefault("$defs", {}).update(devices_defs)
            schema["properties"]["devices"] = {
                **schema["properties"]["devices"],
                **devices_schema,
            }
            # The schema is for a file in an editor, and a file may be a layer: `extends`
            # is stripped before validation (`resolve_layers`), and a `null` entry deletes
            # what an earlier layer declared (`merge`) -- both are documented rig-file syntax.
            schema["properties"]["extends"] = {
                "type": "array",
                "items": {"type": "string"},
                "title": "Extends",
                "description": "This file's own bases, merged in order before its own keys.",
            }
            removed = {"type": "null", "title": "Removed by this layer"}
            for section in ("links", "devices", "controllers"):
                entry = schema["properties"][section].get("additionalProperties")
                if not isinstance(entry, dict):
                    continue
                # Appended to an existing `oneOf` rather than wrapping it: the dashboard reads
                # `devices…additionalProperties.oneOf` and `links…discriminator` as they are.
                if isinstance(entry.get("oneOf"), list):
                    entry["oneOf"] = [*entry["oneOf"], removed]
                else:
                    properties = schema["properties"][section]
                    properties["additionalProperties"] = {"oneOf": [entry, removed]}
            return schema
        return super().model_json_schema(**kwargs)

    @model_validator(mode="before")
    @classmethod
    def _no_legacy_sections(cls, data: Any) -> Any:
        """The legacy sections fail with one message that says where the new shape is."""
        if isinstance(data, dict) and any(section in data for section in LEGACY_SECTIONS):
            raise ValueError(LEGACY_MESSAGE)
        return data

    @model_validator(mode="before")
    @classmethod
    def _keys(cls, data: Any) -> Any:
        """Every name the file declares is a key, made canonical (D-077, D-079).

        `wet-pump` and `wet_pump` are one name: either is accepted, `_` is kept, and a
        file that declares both is refused naming them. A device's `link` and a
        controller's `measured` are taken the same way, so either spelling finds it.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if isinstance(data.get("name"), str):
            data["name"] = check_key(data["name"], "rig name")
        board = data.get("board")
        if isinstance(board, str) and not board.lower().endswith(SUFFIXES):
            data["board"] = check_key(board, "board")
        if isinstance(links := data.get("links"), dict):
            data["links"] = check_keys(links, "link")
        if isinstance(devices := data.get("devices"), dict):
            data["devices"] = {
                name: _canonical_link(entry)
                for name, entry in check_keys(devices, "device").items()
            }
        if isinstance(controllers := data.get("controllers"), dict):
            keyed: dict[str, Any] = {}
            for output, entry in controllers.items():
                address = check_address(output, "controller")
                if address in keyed:
                    raise ValueError(f"controller {output!r} is given twice: `-` and `_` are one")
                if isinstance(entry, dict) and isinstance(entry.get("measured"), str):
                    entry = {**entry, "measured": canonical_key(entry["measured"])}
                keyed[address] = entry
            data["controllers"] = keyed
        return data

    @model_validator(mode="after")
    def _consistent(self) -> RigConfig:
        """Everything named in the file is declared in it, once, in one namespace.

        Device names are the table's keys, so a duplicate is the loader's
        to refuse; what is checked here is a reserved name, an unknown
        driver, a driver's own fields its config refuses (a `pymeasure`
        `instrument` outside the library, say), an undeclared link, and a
        controller address without a dot.
        Before anything is built, so the file fails with one clear message
        instead of a build-time error part-way through.
        """
        catalogs = get_catalog()
        for name, entry in self.devices.items():
            if name in RESERVED_NAMES:
                raise ConflictError(f"Name {name!r} is reserved as a route segment")
            driver = catalogs.devices.get(entry.driver)
            if driver is None:
                raise ValueError(f"device {name!r}: driver {entry.driver!r} is not registered")
            if not issubclass(driver, DriverConfig):
                raise ValueError(
                    f"device {name!r}: {entry.driver!r} is a {driver.__name__}, not a device driver"
                )
            try:  # the driver's own fields, as build() will take them
                driver.model_validate(entry.driver_config)
            except ValidationError as e:
                problems = "; ".join(
                    f"{'.'.join(map(str, error['loc'])) or entry.driver}: {error['msg']}"
                    for error in e.errors()
                )
                raise ValueError(f"device {name!r}: {problems}") from e
            link = entry.driver_config.get("link")
            if isinstance(link, str) and link not in self.links:
                raise ValueError(f"link {link!r} is not declared; links are {sorted(self.links)}")
            _check_inputs(name, entry, driver.device_class())
        _refuse_input_cycles(self.devices)
        for output, controller in self.controllers.items():
            if "." not in output:
                raise ValueError(f"controller {output!r} must be a 'node.signal' address")
            if "." not in controller.measured:
                raise ValueError(
                    f"controller {output!r}: measured {controller.measured!r}"
                    " must be a 'node.signal' address"
                )
        if sum(c.is_default for c in self.controllers.values()) > 1:
            raise ValueError("only one controller can be the default")
        if self.clock is not None and not is_simulated(self.links):
            raise ValueError("`clock` is only for a rig whose links are all sim_* or fake_*")
        return self

    @property
    def simulated(self) -> bool:
        return is_simulated(self.links)

    def build(
        self, clock: Clock | None = None, start: bool = True, catalogs: Catalogs | None = None
    ) -> Rig:
        """Links, then devices, then their bound inputs, then controllers.

        Args:
            clock: The rig's timebase. Default: what the file's `clock` says;
                a simulated rig with none gets a scaled clock at 1x, so its
                speed can be changed while it runs.
            start: Poll the devices on their periods. False adds them
                without polling, for a caller that will drive reads itself.
            catalogs: Where each device's driver is looked up. Default:
                [get_catalog][flyball.model.catalog.get_catalog].
        """
        catalogs = catalogs or get_catalog()
        if clock is None and self.simulated:
            from flyball_sim.clock import ScaledClock, SteppedClock

            entry = self.clock or ClockEntry()
            clock = SteppedClock() if entry.stepped else ScaledClock(entry.speed)

        rig = Rig(self.name)
        rig.link_entries = Keyed.of(self.links)
        rig.files = list(self.files)
        rig.header = {
            k: v
            for k, v in canonical(self).items()
            if k not in ("name", "links", "devices", "controllers")
        }
        if clock is not None:
            rig.clock = clock
        if self.runner is not None:
            reads = self.runner.reads
            rig.polling.defaults = ReadPolicy(reads.fail_after, tuple(reads.backoff_s))
        # Build everything before anything runs: a failure part-way leaves no
        # thread polling and no name claimed for a retry to trip on, and no
        # link (a serial port, a socket) held open behind it.
        built_devices: list[Device] = []
        built_links: dict[str, Any] = {}
        try:
            for name, config in self.links.items():
                built_links[name] = config.build()
            rig.links = Keyed.of(built_links)
            for name, entry in self.devices.items():
                device = entry.build(name, built_links, catalogs)
                rig.add_device(device)
                rig.entries[name] = entry
                built_devices.append(device)
            for name, entry in self.devices.items():
                rig.bind_inputs(rig.devices[name], entry.inputs)
            for name, entry in self.devices.items():
                for path, permissive in (entry.permissive or {}).items():
                    rig.permit(rig.devices[name].signals[path], permissive)
            for output_address, controller in self.controllers.items():
                output = rig.resolve(output_address)
                if not isinstance(output, Signal):
                    raise ValueError(f"controller {output_address!r} is not a signal")
                measured = rig.resolve(controller.measured)
                if not isinstance(measured, Signal):
                    raise ValueError(
                        f"controller {output_address!r}: measured {controller.measured!r}"
                        " is not a signal"
                    )
                rig.attach_controller(
                    output,
                    measured,
                    law=controller.law,
                    feedforward=controller.feedforward,
                    is_default=controller.is_default,
                    min_period_s=controller.min_period_s,
                    setpoint_period_s=controller.setpoint_period_s,
                    on_fault=controller.fault_policy(),
                    label=controller.label,
                )
        except Exception:
            for device in built_devices:
                rig.release(device.name)
            for link in built_links.values():
                close = getattr(link, "close", None)
                if close is not None:
                    with suppress(Exception):
                        close()
            raise
        if start:
            for device in built_devices:
                rig.start_polling(device)
        rig.loaded = rig.document()
        rig.saved_overlay = _saved_overlay(rig.files)
        said = logging.getLogger("flyball.rig")  # the rig's own: what `rig check` cannot see
        for row in stop_plan(rig):
            for warning in row["warnings"]:
                said.warning("stop: %s: %s", row["address"], warning)
        return rig


_models: dict[tuple[tuple[str, ...], tuple[str, ...]], type[RigConfig]] = {}


def rig_model(catalogs: Catalogs | None = None) -> type[RigConfig]:
    """[RigConfig][flyball.runtime.config.RigConfig] typed with every type registered now.

    Built once per set of registered types and cached, so validating many
    files costs one model.

    Args:
        catalogs: Default: [get_catalog][flyball.model.catalog.get_catalog].
    """
    catalogs = catalogs or get_catalog()
    key = (tuple(sorted(catalogs.devices.names())), tuple(sorted(catalogs.links.names())))
    if key not in _models:
        links = Config.union(*registered("link", catalogs))
        _models[key] = create_model(
            "RigConfig",
            __base__=RigConfig,
            links=(dict[str, links], Field(default_factory=dict)),  # type: ignore[valid-type]
        )
    return _models[key]


def _check_inputs(name: str, entry: DeviceEntry, device: type[Device] | None) -> None:
    """Every input the driver declares is bound to an address or a number, and no other name.

    An input has no default (C12): one left out is refused here, so `rig check` says so.
    """
    declared = {} if device is None else device.INPUTS
    if not declared:
        return
    if missing := [n for n in declared if n not in entry.inputs]:
        raise ValueError(
            f"device {name!r}: input {', '.join(repr(n) for n in missing)} is neither bound nor"
            " a number: give `inputs: {"
            + ", ".join(f"{n}: <address or number>" for n in missing)
            + "}`"
        )
    if unknown := [n for n in entry.inputs if n not in declared]:
        raise ValueError(
            f"device {name!r}: {', '.join(repr(n) for n in unknown)} is not an input of"
            f" {entry.driver!r}; it has {', '.join(repr(n) for n in declared)}"
        )


def _refuse_input_cycles(devices: Mapping[str, DeviceEntry]) -> None:
    """A cycle through `inputs:` -- a device following itself, through others or not -- is refused.

    Device by device, by the first segment of each address; the path is named.
    """
    follows = {
        name: [
            (input_name, source)
            for input_name, source in entry.inputs.items()
            if isinstance(source, str) and source.partition(".")[0] in devices
        ]
        for name, entry in devices.items()
    }
    for start in devices:
        stack: list[tuple[str, list[str]]] = [(start, [])]
        seen: set[str] = set()
        while stack:
            name, path = stack.pop()
            for input_name, source in follows[name]:
                step = [*path, f"{name}.inputs.{input_name} <- {source}"]
                target = source.partition(".")[0]
                if target == start:
                    raise ValueError("a cycle through inputs: " + "; ".join(step))
                if target not in seen:
                    seen.add(target)
                    stack.append((target, step))


def _driver_configs(catalogs: Catalogs) -> tuple[type[DriverConfig[Any]], ...]:
    """Every registered device driver, in type order."""
    return tuple(
        config for config in registered("driver", catalogs) if issubclass(config, DriverConfig)
    )


def _devices_schema(catalogs: Catalogs) -> tuple[dict[str, Any], dict[str, Any]]:
    """The `devices` property's schema, and the `$defs` it needs.

    Built by hand from the installed `Catalogs` rather than inferred: a
    device entry's driver fields sit flat beside the envelope, keyed on
    `driver:` (`DeviceEntry` keeps them as its extra keys, not a pydantic
    discriminated union), so pydantic alone cannot describe them. One
    variant per registered driver; before any driver registers, `devices`
    is just a plain `DeviceEntry` map.
    """
    base = DeviceEntry.model_json_schema(ref_template="#/$defs/{model}", schema_generator=Titled)
    defs: dict[str, Any] = dict(base.get("$defs", {}))
    envelope = {k: v for k, v in base["properties"].items() if k != "driver"}
    drivers = _driver_configs(catalogs)
    if not drivers:
        defs["DeviceEntry"] = base
        return {"additionalProperties": {"$ref": "#/$defs/DeviceEntry"}}, defs
    variants = []
    for driver in drivers:
        driver_schema = driver.model_json_schema(
            ref_template="#/$defs/{model}", schema_generator=Titled
        )
        defs.update(driver_schema.pop("$defs", {}))
        device = driver.device_class()
        declared = [] if device is None else list(device.INPUTS)
        properties = {
            **envelope,
            "driver": {"const": driver.type_name},
            **driver_schema.get("properties", {}),
        }
        if declared:  # no default: each is bound to an address or a number (C12)
            properties["inputs"] = {
                **envelope["inputs"],
                "required": declared,
                "propertyNames": {"enum": declared},
            }
        variants.append({
            "type": "object",
            "title": driver.type_name,
            **({"description": d} if (d := driver_schema.get("description")) else {}),
            "properties": properties,
            "required": [
                "driver",
                *driver_schema.get("required", []),
                *(["inputs"] if declared else []),
            ],
            "not": {"required": ["config"]},
        })
    # A layer may add to a device a base declared (`inputs`, a label, one driver field)
    # without repeating its driver: envelope keys and any driver field, and no `driver`.
    overlay = {
        "type": "object",
        "title": "overlay of a device declared in a base",
        "properties": envelope,
        "not": {"required": ["driver"]},
    }
    return {"additionalProperties": {"oneOf": [*variants, overlay]}}, defs


def render_document(loaded: dict[str, Any]) -> dict[str, Any]:
    """A rig as a rig file, in the form [Rig.document][flyball.rig.rig.Rig.document] gives.

    `loaded` holds `name`, the header keys (`label`, `board`, `clock`, `recording`), `links` as the
    file writes them (`{type, ...}`), `devices` as entries and `controllers` as
    [ControllerEntry][flyball.runtime.config.ControllerEntry]s. Defaults are left out, as a
    hand-written file leaves them; a law's or feedforward's `type` is kept, since the file
    needs it.
    """
    config = RigConfig.model_validate(loaded)
    document = config.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    document["links"] = loaded["links"]
    for key in ("devices", "controllers"):
        document.setdefault(key, {})
    controllers: dict[str, ControllerEntry] = loaded["controllers"]
    for name, entry in controllers.items():
        rendered = document["controllers"][name]
        if entry.law is not None:
            rendered.setdefault("law", {})["type"] = entry.law.type
        if entry.feedforward is not None:
            rendered.setdefault("feedforward", {})["type"] = entry.feedforward.type
    return document


def document_of(config: RigConfig) -> dict[str, Any]:
    """`config` as the rig it builds would render itself (`Rig.document()`), without building.

    What a rig edit is saved as, and compared by: the running rig's document and one read
    from files are then in the same form.
    """
    header = {
        k: v
        for k, v in canonical(config).items()
        if k not in ("name", "links", "devices", "controllers")
    }
    links = {
        name: {
            "type": link.type_name,
            **link.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
        }
        for name, link in config.links.items()
    }
    return render_document({
        "name": config.name,
        **header,
        "links": links,
        "devices": dict(config.devices),
        "controllers": dict(config.controllers),
    })


def canonical(config: RigConfig) -> dict[str, Any]:
    """`config` as the canonical document -- what `rig check` prints.

    Every device entry is its envelope keys, then its driver's fields flat
    beside them; dumping drops every `null`, since a format like TOML has no
    way to write one.
    """
    return config.model_dump(mode="json", exclude_none=True)


# endregion
# region Boards


class Board(BaseModel):
    """A machine's I/O, as data: the links it has and what its pins are called.

    Lives in a file on the board path, not in a package: a new board is a
    file, and a wrong line number is an edit.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    links: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pins: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Header label -> the device fields it stands for: `{link, line}`.",
    )


BOARDS_ENV = "FLYBALL_BOARDS"
"""Directories to look in for `<name>.toml`, colon-separated, before the defaults."""

BOARD_DIRS_GROUP = "flyball.board_dirs"
"""Entry-point group an installed package registers its board profiles' directory under."""


def board_dirs(near: Path | None = None) -> Iterator[Path]:
    """Where a board name is looked up, in order.

    `$FLYBALL_BOARDS`; each installed package's own profiles (`extensions/linux`
    ships `rpi4`, `rpi5`, `beaglebone_black`, `generic`, `sim` this way); a
    `boards/` directory beside the rig file or in any directory above it, for
    profiles of your own; `~/.config/flyball/boards`; `/etc/flyball/boards`.
    """
    for entry in os.environ.get(BOARDS_ENV, "").split(os.pathsep):
        if entry:
            yield Path(entry).expanduser()
    yield from discover_paths(BOARD_DIRS_GROUP)
    if near is not None:
        for directory in (near.resolve(), *near.resolve().parents):
            yield directory / "boards"
    yield Path("~/.config/flyball/boards").expanduser()
    yield Path("/etc/flyball/boards")


def find_board(name: str, near: Path | None = None) -> Path:
    """The file for `name`: a path (relative to the rig file), or a name on the board path.

    Raises:
        NotFoundError: With every directory that was looked in.
    """
    base = near if near is not None else Path.cwd()
    if name.lower().endswith(SUFFIXES):
        path = base / name
        if path.is_file():
            return path
        raise NotFoundError(f"board file {path} does not exist")
    key = check_key(name, "board")
    # A file name may have either spelling; the name is looked up in both (D-079).
    stems = dict.fromkeys((key, key.replace("_", "-")))
    for directory in board_dirs(base):
        for stem in stems:
            for suffix in SUFFIXES:
                if (candidate := directory / f"{stem}{suffix}").is_file():
                    return candidate
    looked = ", ".join(str(d) for d in board_dirs(base))
    raise NotFoundError(f"no board {name!r}; looked in {looked}")


def load_board(path: str | Path) -> Board:
    return Board.model_validate(load_document(path))


def apply_board(document: dict[str, Any], board: Board) -> dict[str, Any]:
    """The document with the board's links underneath its own and its pins resolved.

    A device entry with `pin = "LABEL"` gets the driver fields the board
    gives that label, flat beside the envelope; fields the entry already
    has win. Unknown labels are an error.
    """
    out = dict(document)
    out["links"] = {**board.links, **document.get("links", {})}

    def fields_of(label: Any, where: str) -> dict[str, Any] | None:
        if not isinstance(label, str):
            return None
        try:
            return board.pins[label]
        except KeyError:
            raise NotFoundError(
                f"{where}: pin {label!r} is not on this board; it has {sorted(board.pins)}"
            ) from None

    def resolved(entry: dict[str, Any], where: str) -> dict[str, Any]:
        if (fields := fields_of(entry.get("pin"), where)) is not None:
            return {**fields, **{k: v for k, v in entry.items() if k != "pin"}}
        return entry

    devices = document.get("devices")
    if isinstance(devices, dict):
        out["devices"] = {
            name: resolved(entry, f"devices.{name}") if isinstance(entry, dict) else entry
            for name, entry in devices.items()
        }
    return out


# endregion
# region Loading


def resolve_document(path: str | Path) -> tuple[dict[str, Any], Path | None]:
    """The plain document with its board applied, and the board file used, if any."""
    path = Path(path)
    document = load_document(path)
    if not isinstance(document, dict):
        raise ValueError(f"{path}: a rig file is a mapping")
    board_name = document.get("board")
    if not isinstance(board_name, str):
        return document, None
    board_path = find_board(board_name, path.parent)
    return apply_board(document, load_board(board_path)), board_path


def resolve_documents(
    paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()
) -> tuple[dict[str, Any], list[Path]]:
    """`resolve_document`, generalised to a rig laid out as several files.

    `paths` is one file or several, later overlaying earlier (see
    [resolve_layers][flyball.runtime.overlay.resolve_layers]); each file's
    own `extends` is resolved first. A `board` is looked up relative to the
    *first* file, exactly as `resolve_document` looks one up relative to its
    one file, and applied to the merged document.

    Returns:
        The merged, board-applied document, and every file that
        contributed: the layers, in the order first read, then the board
        file, if one was used.
    """
    path_list = [Path(paths)] if isinstance(paths, (str, Path)) else [Path(p) for p in paths]
    return _layered(path_list, [*path_list, *saved_overlays(path_list[0])], sets)


def resolve_with_overlay(
    paths: Sequence[str | Path], sets: Sequence[str], overlay: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Path]]:
    """`resolve_documents`, with `overlay` in place of the saved overlay (`<file>.d/added.*`).

    What a start from the same command line would load once `overlay` is saved there, read
    before it is written; an empty `overlay` is a start with none. The other files in the
    `.d/` directory keep their places (the directory's sorted order, `added.*` among them).
    """
    path_list = [Path(p) for p in paths]
    added = saved_overlay_path(path_list[0])
    others = [p for p in saved_overlays(path_list[0]) if p.name != added.name]
    layers: list[Path | Mapping[str, Any]] = list(path_list)
    for path in sorted([*others, added]):
        if path != added:
            layers.append(path)
        elif overlay:
            layers.append(overlay)
    return _layered(path_list, layers, sets)


def _layered(
    path_list: list[Path], layers: Sequence[Path | Mapping[str, Any]], sets: Sequence[str]
) -> tuple[dict[str, Any], list[Path]]:
    from flyball.runtime.overlay import resolve_layers

    document, files = resolve_layers(layers, sets)
    board_name = document.get("board")
    if not isinstance(board_name, str):
        return document, files
    board_path = find_board(board_name, path_list[0].parent)
    return apply_board(document, load_board(board_path)), [*files, board_path]


def saved_overlay_path(first: Path) -> Path:
    """Where `POST /api/rig/save` writes by default: `<file>.d/added.<suffix>` beside `first`."""
    return first.with_name(first.name + ".d") / f"added{first.suffix}"


def _saved_overlay(files: Sequence[Path]) -> dict[str, Any]:
    """The saved overlay's document if this rig loaded one (it is among `files`), else `{}`."""
    if not files:
        return {}
    target = saved_overlay_path(files[0])
    if not any(f.resolve() == target.resolve() for f in files):
        return {}
    document = load_document(target)
    return document if isinstance(document, dict) else {}


def saved_overlays(first: Path) -> list[Path]:
    """The overlays the runner saved beside a rig's first file: `<file>.d/*.<suffix>`, sorted.

    What `POST /api/rig/save` writes by default; loaded after the files
    named on the command line, so a saved addition comes back next start.
    """
    directory = first.with_name(first.name + ".d")
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in SUFFIXES)


def load_rig_config(
    path_or_paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()
) -> RigConfig:
    """Read and validate a rig file, or a layered rig of several, `.toml`, `.yaml` or `.json`.

    Installed packages' configs are discovered first, so their types are valid
    in the file; a `board` is applied before validation.
    """
    ensure_discovered()
    document, files = resolve_documents(path_or_paths, sets)
    config = RigConfig.model_validate(document)
    config.files = files
    return config


def load_rig(path_or_paths: str | Path | Sequence[str | Path], sets: Sequence[str] = ()) -> Rig:
    return load_rig_config(path_or_paths, sets).build()


def rig_schema() -> dict[str, Any]:
    """The rig file's JSON schema, for an editor, with every type installed here."""
    ensure_discovered()
    schema = RigConfig.model_json_schema()
    schema["$schema"] = GenerateJsonSchema.schema_dialect
    return schema


# endregion

__all__ = [
    "BOARDS_ENV",
    "BOARD_DIRS_GROUP",
    "Board",
    "ClockEntry",
    "ControllerEntry",
    "RigConfig",
    "apply_board",
    "board_dirs",
    "canonical",
    "document_of",
    "find_board",
    "is_simulated",
    "load_board",
    "load_rig",
    "load_rig_config",
    "registered",
    "render_document",
    "resolve_document",
    "resolve_documents",
    "resolve_live",
    "resolve_with_overlay",
    "rig_model",
    "rig_schema",
    "role_of",
]
