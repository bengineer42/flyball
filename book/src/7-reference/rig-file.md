# Rig file schema

A rig file describes links, devices and controllers, and builds them in
that order. `.yaml` is the documented form (deep envelopes read better,
and programs are YAML already); `.toml` and `.json` are accepted with the
same shape. This page is the strict schema -- what is validated and how;
[Configuration](../2-config/index.md) walks the same file section by
section with every option.

```python
from flyball.runtime.config import load_rig, load_rig_config, rig_schema

rig = load_rig("rig.yaml")            # validate and build
config = load_rig_config("rig.yaml")  # validate only
schema = rig_schema()                 # JSON Schema for an editor
```

Unknown keys are refused at every level. A device that names a link must
name one declared under `links`; a controller must name an output and a
measured address that resolve; a unit symbol must be one the units table
knows. All of these fail at load with the offending name — `flyball rig
check FILE` reports the same way.

Device and controller tables are **keyed by name**, so a duplicate is a
parse error before flyball sees it, which is why the file needs a strict
YAML loader that rejects duplicate keys (plain PyYAML keeps the last
silently). Links are a separate namespace — a link may share a name with a
device.

Booleans are YAML 1.2's: only `true` and `false` (in any case). The YAML
1.1 words `yes`, `no`, `on` and `off` stay strings, so a GPIO's `on`
signal is a key and `on_stop: off` is the word `off`, not `false`. The same
holds for a program file, a library upload and a `--set` value.

## Top level

| key | type | |
| --- | --- | --- |
| `name` | string | optional |
| `board` | string | a board profile: a name on the board path (`$FLYBALL_BOARDS`, `boards/` beside or above the file, `~/.config/flyball/boards`, `/etc/flyball/boards`), or a path relative to the file; its `links` are added underneath the file's own, and `pin: "LABEL"` on a device resolves against its `pins` |
| `recording` | bool | open a session when the runner starts |
| `clock` | `{speed?, stepped?}` | run the rig's time faster (`speed`, default 1×), or only when stepped (`stepped`, for a batch run or a test); refused unless every link is `sim_*`/`fake_*` |
| `extends` | `[path, …]` | this file's own bases, resolved and merged (in order) before this file's own keys are layered on top; the command line's own overlay list still wins |
| `runner` | `RunnerConfig` | how the process serves -- port, who may reach a bare runner (`auth`), how `flyball run`'s front serves it ([`front`](#the-front)), what the API may do, where the store and the directories are, the default `reads: {fail_after: 3, backoff_s: [1, 2, 5, 15, 60]}` for every device; what shutting down does to outputs (`on_shutdown: stop | keep`, default `stop`); not part of the rig (not in its document or versions; a save over an existing file keeps that file's own section), overridden by the flags of the same names. Every key: [The runner section](../2-config/runner.md) |
| `links` | `{name: Link}` | declared once, referred to by name |
| `devices` | `{name: DeviceEntry}` | the envelope + the driver's own config, [flat beside it](#devices) |
| `controllers` | `{output-address: ControllerEntry}` | keyed by the demand driven, the controller's output |

## Several files: overlays

A rig is an ordered list of files, later overlaying earlier — the
docker-compose `-f` / kustomize / Hydra pattern:

```
flyball-runner furnace.yaml sim.yaml
flyball rig check furnace.yaml sim.yaml --set devices.furnace.noise=0.3
```

`merge(base, overlay)` (`flyball.runtime.overlay`) is the one rule:
mappings deep-merge key by key, everything else (scalars, lists) replaces
whole, and a key whose overlay value is `null` is removed from the result
— the only way to delete something an earlier layer set (a real link,
before a `sim_*` one takes its place). `--set KEY=VALUE` parses `KEY` as a
dotted path and `VALUE` as a YAML scalar (so `null` deletes there too) and
applies as a one-key overlay on top of every file. `extends:` inside a
file resolves the same way, under that file, before the command line's
files are merged with each other.

After the command line's files, every start also loads the files in
`<first file>.d/` (`lab.yaml.d/*.yaml`, sorted), before `--set`. The
runner's own is `added.<suffix>`: where a [rig edit](../glossary.md) made
over the API is saved, as the difference from your files, so the edit
survives a restart and a later change to your file still applies where the
edit did not touch it. Delete it to go back to your files alone; the one an
edit replaced is kept as `added.<suffix>.prev`
([Building a rig while it runs](../1-running/runner/building.md)).

The point of an overlay is that it swaps the **drivers** behind the same
device and signal names, so every address, controller, dashboard, program
and recorded session is identical whether the rig is real or simulated.
`examples/furnace/rig.yaml` demonstrates the pattern in one file (a
`sim_daq`/`sim_drive` pair standing in for a thermocouple DAQ and an SSR
bank that don't exist yet); `examples/humidity/rig-multi-sensor.yaml` + `sim.yaml` is
the real two-file form — read both, and [the humidity book](https://bengineer42.github.io/humctrl/2-config/) on them. `examples/site/*.yaml` is the third
layer: a file per deployment holding only `extends` and `runner:`.

## Devices

Every device entry has an **envelope** — flyball's own keys, the same for
every driver — with the driver's own config flat beside it: every other
key is the driver's (checked at import: a driver's config may not declare a
field named like an envelope key). A nested `config:` is refused.

| envelope key | type | |
| --- | --- | --- |
| `driver` | string | which driver builds this device; a type registered in the driver catalog |
| `label` | string, optional | shown instead of the name |
| `poll_s` | number, optional | inherited down the tree; a namespace's or signal's own wins |
| `signals` | `{name: SignalMeta \| NamespaceMeta}` | per-signal metadata and access restriction — never adds access the driver did not declare |
| `inputs` | `{input: address \| number}` | what each input follows: an address on another device, resolved once at build to its `Signal` (which must publish) or `Node` (something under it must), or a finite number, a constant. The device reads it through its `InputBinding` (`self.<input>.value`). Every input the driver declares must be given one and no other name -- an input has no default; a cycle through `inputs:` is refused, the path named. [Devices: binding one device to another](../2-config/devices/index.md#binding-one-device-to-another) |
| `reads` | `{fail_after?, backoff_s?, give_up_after_s?}`, optional | reads that raise in a row before the device is `offline` (integer ≥ 1), the waits between retries while offline (non-empty, each finite and > 0; the last repeats), and how long after going offline to stop retrying (finite, > 0; `null`: never). A key left out is `runner.reads`', then `3` / `[1, 2, 5, 15, 60]` / never. [Devices: `reads`](../2-config/devices/index.md#reads) |
| `retry_max_age_s` | number, optional | how long a value a failed write kept may wait to be sent again (finite, > 0); older is dropped with a `write_dropped` event, not sent. Unset: 60 s. [Devices: a write that fails](../2-config/devices/index.md#a-write-that-fails) |
| `stop` | `{path: number \| "keep"}`, optional | what a stop writes to each writable demand (by path under the device): a finite number inside the signal's static limits, or `keep` (leave it as it is); overrides the driver's `off`. Refused whole on a device whose driver has a stop command. [Devices: `stop`](../2-config/devices/index.md#stop-what-a-stop-writes) |
| `on_shutdown` | `stop` \| `keep`, optional | what the runner's shutdown does to this device; unset: `runner.on_shutdown`. [Devices: `on_shutdown`](../2-config/devices/index.md#on_shutdown-what-shutting-down-does) |
| `permissive` | `{path: {signal, above?, below?}}`, optional | a write to the demand at `path` is refused unless `signal`'s value is `> above` and `< below` (at least one; finite; `above < below`); fails closed; the demand's stop value is always permitted. [Devices: `permissive`](../2-config/devices/index.md#permissive-a-write-only-while-another-signal-allows-it) |

```yaml
devices:
  wet_supply: { driver: sht4x, label: Wet supply, poll_s: 5, link: i2c1, i2c_address: 0x46 }

  hum_sensors:
    driver: sht4x_set
    label: Humidity sensors
    poll_s: 1
    link: i2c1
    sensors: { chamber: { i2c_address: 0x44 }, dry: { i2c_address: 0x45 }, wet: { i2c_address: 0x46 } }
    signals:
      chamber: { signals: { humidity: { warning: [20, 80] } } }
      dry:     { poll_s: 5 }
```

(from the plan's worked example — `examples/humidity/rig-multi-sensor.yaml` is the real
file this became).

A `SignalMeta` is `{label, range, precision, warning, alarm, on_no_value, poll_s,
stale_after_s, limits, max_rate, tags, record, access, readable, published, writable}`
(`record: false` leaves the signal out of a recording started with the
default selection):
the first group replaces metadata the driver declared (`tags` are added to the
driver's: `{line: dry}`, a grouping across the tree the UI titles and
filters by; `stale_after_s` is seconds without a reading after which the
rig pushes `stale` on the signal (default `max(3·poll_s, 5 s)` while its
device is polled; a pushed signal is judged only with its own --
[Liveness](../2-config/devices/index.md#liveness-a-signal-that-stops-arriving)),
and a controller regulated from it holds on a reading that arrives older
than this; `on_no_value` is `fire` or `ignore`, what a
banded signal does while it has no value because of a fault (`fire`:
`band_unknown` after `max(2·poll_s, 1 s)` of fault time; unset: `fire` with an `alarm`
band, `ignore` with only `warning` --
[Bands](../2-config/devices/index.md#a-banded-signal-with-no-value));
`max_rate` is `{per_second: N}` (or `per_minute`, `per_hour`, ...), the
fastest a demand may move -- a faster one is clamped to the largest step the
elapsed time allows, up to one update period (`poll_s`, else the controller's
`min_period_s`, else 1 s), not refused; `limits` only narrows: a demand is clamped
to the intersection of the driver's limits and the file's, resolved at each
demand, and a file bound past a numeric driver bound is refused at load);
`access` names the set to keep (`"r"`), and
`readable`/`published`/`writable` drop one flag each and take only
`false` — the driver declares what it can honour, the file cannot add to
it, unless the driver also names a ceiling for that signal (a Python-level
option, not a rig-file key), in which case `access` may ask for anything up
to and including it. A `NamespaceMeta` is `{label, poll_s, tags, signals}`, recursing
the same way into a namespace's own children; its `tags` apply to every
signal under it, a signal's own winning.

A key left out of the metadata leaves the driver's value; a key given as
`null` clears it to the unset default (`label` the titlecased name, a band
none, `poll_s` inherited) -- `limits: null` clears only the file's
narrowing, never the driver's limits. `poll_s` (on a device, namespace or
signal) and `stale_after_s` must be finite and above zero; `0`, a negative
number or `.nan` is refused at load.

## Links

Every link is a typed config, declared once under `links:` and referred
to by name from a device's `link` field. The types and every field, one
section each: [Links](../2-config/links.md); the board types
(`i2c`, `spi`, `gpio`, `pwm`, `onewire` and their fakes):
[Boards and Linux I/O](../2-config/boards.md).

## Drivers

`driver:` names a type registered in the driver catalog; the driver's
own fields sit flat beside the envelope. Every shipped
driver with its fields and an example entry: [Supported drivers](../2-config/devices/drivers.md);
why those fields and where else they appear: [Where a device's options come from](../2-config/devices/generated.md).
Any device entry may say `pin: "LABEL"` instead
of the link/line fields, when the file has a `board`: the board's fields
for that label fill in, and anything the entry already gives wins.

The engine's one built-in driver is `values`: `values: {name: {initial,
unit?, quantity?, label?, limits?}}`, one `setting` `rpw` per entry,
published from build, whose last write is kept in the store and restored
while `initial` is unchanged -- [`values`](../2-config/devices/drivers.md#values).

## Controllers

Keyed by the **output's address** — a controller is named by the demand it
drives, its output. The output must be a demand (`role` `demand`) with `W`;
a setting, or an `RP` demand, is refused when the rig is built.

| key | type | |
| --- | --- | --- |
| `measured` | address | the measured signal: a published (`P`) signal, what is regulated |
| `law` | `{type, ...gains}` | e.g. `{type: pi, kp: 0.2, ki: 0.05}`; omit for none |
| `feedforward` | `{type, ...}` | maps the measured signal's unit to the output's: `identity`, `none`, `affine {gain, bias, rate_gain?}`, `table {points, rate_gain?}`; omit for `identity` when the units agree, else `none` |
| `default` | bool | the controller a command means when it names none; at most one per file |
| `min_period_s` | number, optional | update the law at most this often |
| `setpoint_period_s` | number, optional | while following a moving setpoint, re-apply its feedforward this often between readings (> 0); unset: `max(0.1 s, poll_s / 4)` from the measured signal's `poll_s`. [Controllers](../2-config/controllers.md#a-setpoint-that-moves-faster-than-its-sensor) |
| `on_fault` | `freeze` \| `manual` \| `stop` \| `stop_device` \| `{freeze_s, then}`, optional | what it does once its measured signal has been faulty for its wait; default `freeze`. `freeze_s` finite, ≥ 0; `then` one of `manual`, `stop`, `stop_device`. `stop` on an output whose stop resolves to `keep` is refused when the rig is built. [Controllers: `on_fault`](../2-config/controllers.md#on_fault-what-a-controller-does-about-a-faulty-source) |

```yaml
controllers:
  heaters.heater1: { measured: furnace.zone1, law: { type: pi, kp: 100, ki: 0.15, tt_s: 30 } }
  heaters.heater2:
    measured: furnace.zone2
    law: { type: pi, kp: 100, ki: 0.15, tt_s: 30 }
    feedforward: { type: table, rate_gain: 3000, points: [[20, 0], [200, 289.4], [400, 659.8]] }
    default: true
```

(`examples/furnace/rig.yaml`, abridged). `rate_gain` (`affine`,
`table`) adds `rate_gain * rate` to the output, `rate` being the
setpoint's own rate of change in the measured unit *per second* (zero off
a ramp): output unit per measured-unit-per-second — a zone's
`capacity_j_per_k` (J/K = W per °C/s) is the extra power a ramp needs to
charge its own thermal mass. Not on `identity`: that feedforward already
hands the output the measured signal's own unit, so a rate term there would be a
lead compensator, a different job from the plant-capacity model this is.

## The front

`runner.front` is read by the Go front `flyball run` starts; `flyballd`
reads the same keys from the top level of `flyballd.yaml`, beside its own.
`flyball-runner` never acts on it, and a block that does not validate is
only a warning to it; `flyball rig check` holds it to this schema. The block
is read with the file's `extends` resolved; a file whose `extends` cannot
be, an unknown key or a wrong type makes the front fall back to the `local` shape
on `127.0.0.1` ([Access](../1-running/runner/access.md#when-a-setting-is-wrong)),
as does every error the table marks *falls back*; a key marked *warns* is
ignored with one warning line and its default used. When `auth` asked for
anything but `local`, or the block cannot be read, a fallback answers `503` on `listen` and serves the
`local` shape on a fresh loopback port (or on `<socket>.local` beside a
`unix:` one) instead.

| key | type | default | |
| --- | --- | --- | --- |
| `listen` | `host:port` or `unix:/path` | `127.0.0.1:8000` (`flyballd`: `127.0.0.1:9000`) | where the front listens; `flyball run --listen` wins. A unix socket file nobody answers on is replaced; one another process answers on is left, and the front does not serve (the rig runs on). The socket is made `0660`; a directory anyone may write to without the sticky bit, or the front's own directory with any permission for others, is refused the same way ([who may connect](../1-running/runner/access.md#behind-an-identity-proxy)). Unparseable: falls back |
| `auth` | `local` / `password` / `proxy` / `sso` | `local` | the [shape](../1-running/runner/access.md#shapes-who-gets-in). `local` asked for a non-loopback `listen` falls back unless the run says `--insecure-open`; `sso` falls back (not in this release) |
| `password` | string | none | `auth: password`'s admin password, as the `$scrypt$` line `flyball password` prints; missing or plain text falls back |
| `anonymous` | `none` / `read` | `none` | what a caller with no credential may do under `password` and `proxy`, served only by an IP address, a loopback name, the machine's own name (`hostname`, `<hostname>.local`) or `url`'s host (DNS rebinding; signing in answers any name); the `local` shape ignores it. Another value warns |
| `url` | `http(s)://host[:port]` | none | the external address: its host joins the `Host` allow-list (with the loopback names; under `--insecure-open` also IP addresses and the machine's own name), its origin the `Origin` allow-list; `https` makes the cookie `Secure` and `__Host-flyball`, and the scheme the principal reports when the peer is this machine or in `trusted_proxies` (a peer elsewhere without TLS is `http`). A path, query or user part falls back |
| `tls` | `{cert, key}` | none | PEM files the front serves HTTPS from, TLS 1.2 at least; re-read every 10 s and on `SIGHUP`, the last good pair kept. Unreadable at start: falls back |
| `proxy` | table | none | `auth: proxy`'s identity layer: [below](#proxy-presets). Missing, or one that cannot be vouched for: falls back |
| `session` | duration | `12h` | how long a session lasts without a request from it (`12h`, `2d`); it ends after 7 days whatever. An open dashboard polls, which counts, so it keeps its session to the 7 days. Unparseable warns |
| `trusted_proxies` | `[IP or CIDR, …]` | `[]` | peers whose `X-Forwarded-For` the front believes: when the connection comes from one, the client is the right-most address in that header not in the list. That address is what the sign-in limit counts and the audit and principal record. List the proxies themselves, not the network they share with clients: a client whose own address is in the list is believed too, so it can name any address it likes. A bad entry warns and is dropped |
| `tokens` | `{default_lifetime, max_lifetime}` | 90 days, 365 days | [named-token lifetimes](#token-lifetimes) |
| `uv` | bool | `false` | `flyball run` only: run `flyball-runner` via `uv run --project <the rig file's directory>` |

The front's cookie is `flyball-<port>` over plain HTTP and `__Host-flyball`
when `tls` or an `https` `url` is set: `HttpOnly`, `SameSite=Lax`,
`Path=/`. A cookie is not port-isolated -- every service on the same host
name receives it -- so give each front its own host name where that
matters.

### Token lifetimes

Every named token expires. `tokens:` may only tighten the built-ins:

| key | built-in | |
| --- | --- | --- |
| `default_lifetime` | `90d` | a token created without a lifetime of its own; at most `max_lifetime` |
| `max_lifetime` | `365d` | the most any token may have; above 365 days is refused |

A token of `kind: agent`, one created over plain HTTP from another
machine (no TLS on the hop to the front, whatever `url` says), or one asking for a scope above `read` from the admin session
(`flyball login --scope`, or anything else that holds the password and
calls `POST /api/auth/tokens` directly) lives at most 30 days, or
`max_lifetime` if that is shorter. A lifetime asked for above the cap gets
the cap. Durations are Go's (`36h`) or whole days (`30d`); a bad value
warns and its built-in applies. `flyball token create --config PATH` reads
the same block, so a token made offline gets the same limits.

### Proxy presets

`auth: proxy` takes one `proxy:` block, and `preset` says which proxy is in
front. An **unsigned** preset reads plain headers, which the front believes
only from a peer it can vouch for; a **signed** one verifies a JWT against
the issuer's published keys.

```yaml
proxy: {preset: tailscale}                        # listen: unix:/run/flyball/front.sock; from: unix is the default
proxy: {preset: authelia, grants: {all: ["group:lab-admins"]}}   # over unix
proxy: {preset: authelia, from: [127.0.0.1], secret_file: /etc/flyball/proxy-secret}  # the proxy sends X-Flyball-Proxy-Secret
proxy: {preset: authelia, from: [10.0.5.2]}       # a proxy on another host needs no secret
proxy: {preset: oauth2-proxy}                     # unsigned, over unix
proxy: {preset: oauth2-proxy, issuer: https://idp.lab.org, audience: flyball-client-id}  # Authorization: Bearer <ID token>
proxy: {preset: authentik}                        # unsigned: X-authentik-uid, groups split on |
proxy: {preset: authentik, issuer: https://auth.lab.org/application/o/flyball/}  # X-authentik-jwt
proxy: {preset: pomerium}                         # needs an https url:; optional issuer:, audience:
proxy: {preset: cloudflare, team: lab, audience: <the application's AUD tag>}
proxy: {preset: custom, user_header: X-Lab-User, groups_header: X-Lab-Groups, separator: ";"}
proxy: {preset: custom, jwt: {header: X-Lab-Jwt, jwks_url: https://idp.lab.org/keys, issuer: lab-idp, audience: flyball, algorithms: [ES256]}}
```

| preset | kind | identity from | groups | needs |
| --- | --- | --- | --- | --- |
| `tailscale` | unsigned | `Tailscale-User-Login` (name: `Tailscale-User-Name`) | none | `from:` |
| `authelia` | unsigned | `Remote-User` (name: `Remote-Name`) | `Remote-Groups`, comma-separated | `from:` |
| `oauth2-proxy` | unsigned | `X-Forwarded-User` | `X-Forwarded-Groups`, comma-separated | `from:` |
| `oauth2-proxy` | signed | `Authorization: Bearer <ID token>`, RS256 or ES256, keys by OIDC discovery on `issuer` | the `groups` claim | `issuer:`, `audience:` (the client id) |
| `authentik` | unsigned | `X-Authentik-Uid` (name: `X-Authentik-Name`) | `X-Authentik-Groups`, `|`-separated | `from:` |
| `authentik` | signed | `X-Authentik-Jwt`, RS256 or ES256, keys at `<issuer>/jwks/` | the `groups` claim | `issuer:` (`audience:` optional) |
| `pomerium` | signed | `X-Pomerium-Jwt-Assertion`, ES256, keys at `https://<url host>/.well-known/pomerium/jwks.json` | the `groups` claim | an `https` `url:`; `issuer` and `audience` default to its host |
| `cloudflare` | signed | `Cf-Access-Jwt-Assertion`, RS256, keys at `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` | the `groups` claim | `team:`, `audience:` |
| `custom` | unsigned | `user_header` | `groups_header`, split on `separator` (default `,`) | `from:` |
| `custom` | signed | `jwt.header` (`Authorization` means a bearer token) | the `groups` claim | `jwt: {header, jwks_url, issuer, audience, algorithms}` |

| key | |
| --- | --- |
| `preset` | one of the above; required |
| `from` | unsigned presets: whose headers are believed -- `unix` (the default: the front's own socket, so `listen` must be `unix:/path`; the local user behind each new identity is recorded as `proxy.peer`), or a list of the proxy's IPs or CIDRs (then `listen` must be TCP). `/0` is refused; a range wider than one host (`/32`, `/128`) without `secret_file` warns at start, since every host in it can assert any identity. A loopback address, or one of this host's own, stands for every local process, so it also needs `secret_file` |
| `secret_file` | a file holding a secret of at least 16 characters, not readable by every user, which the proxy sends as `X-Flyball-Proxy-Secret`; a request without it is not believed, one with a wrong one is refused. Works with `from: unix` too, against other processes of the proxy's user ([Access](../1-running/runner/access.md#behind-an-identity-proxy)) |
| `issuer`, `audience` | signed presets: the exact `iss`, and a value `aud` must contain |
| `team` | `cloudflare`: the Access team name (one DNS label) |
| `grants` | grant name → entries. Grants: `all` (every verb) and `viewer` (read); names pending D-034, and an unknown name grants nothing (a warning says so). An entry is a subject, or `group:<id>`. Every grant is on every rig; an identity matching none gets `read` |
| `user_header`, `groups_header`, `separator` | `custom`, unsigned. A header name is letters, digits and `-`; the front's own (`Authorization`, `Cookie`, `Host`, `Origin`, the forwarding headers, `X-Flyball-*`) are refused |
| `jwt` | `custom`, signed: `header`, `jwks_url` (`https`, or `http` on loopback), `issuer`, `audience`, `algorithms` (RS/PS/ES 256–512 and EdDSA; never `none` or `HS*`) |

For a request, the front reads a named token first, then (under
`password`) a session, then the proxy's assertion, and only with none of
them is the caller anonymous. An assertion that is present and wrong -- a
bad signature, a wrong `iss` or `aud`, expired (60 s leeway on `exp`, `nbf`
and `iat`), a second copy of an identity header, a copy under another
spelling (`Remote_User`), groups without a user -- is `401`, never
anonymous. Keys the front cannot fetch make every such request `503`, and
the front logs one line when fetching starts to fail (its kind -- `dns`,
`connect`, `timeout`, `tls`, an HTTP status -- and the host) and one when it
works again; keys are refetched after an hour, and for an unknown key id at most once a
minute. A fetch holds up only the requests that need it, which share it: a
token whose key is already known is checked at once. A proxy identity is `proxy:<issuer>#<subject>`: for an unsigned
preset the issuer is the preset's name (`proxy:authelia#ben`), for a
signed one the token's `iss`. The subject is the proxy's stable id --
never the e-mail address: oauth2-proxy run so that the user header carries
the e-mail (`--prefer-email-to-user`) is refused. authentik signs with its
client secret (HS256) when its provider has no signing key, and HS256 is
refused, so give the provider one.

**Not yet tried against the real products:** Tailscale Serve proxying to a
unix socket, Pomerium's default `iss` and `aud`, authentik's JWT claims,
oauth2-proxy's e-mail-as-user detection, and Cloudflare service tokens
(which carry no `sub`, so they are refused). Each preset follows the
product's documentation; report what differs.

## Example

See [Configuration](../2-config/index.md) and [Integrations](../5-integrations/index.md) for more complete
files, and [the humidity book](https://bengineer42.github.io/humctrl/) for a real two-file (hardware + simulated
overlay) rig.
