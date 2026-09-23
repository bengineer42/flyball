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
name one declared under `links`; a controller must name a source and
target address that resolve; a unit symbol must be one the units table
knows. All of these fail at load with the offending name — `flyball rig
check FILE` reports the same way.

Device and controller tables are **keyed by name**, so a duplicate is a
parse error before flyball sees it, which is why the file needs a strict
YAML loader that rejects duplicate keys (plain PyYAML keeps the last
silently). Links are a separate namespace — a link may share a name with a
device.

## Top level

| key | type | |
| --- | --- | --- |
| `name` | string | optional |
| `board` | string | a board profile: a name on the board path (`$FLYBALL_BOARDS`, `boards/` beside or above the file, `~/.config/flyball/boards`, `/etc/flyball/boards`), or a path relative to the file; its `links` are added underneath the file's own, and `pin: "LABEL"` on a device resolves against its `pins` |
| `recording` | bool | open a session when the runner starts |
| `clock` | `{speed?, stepped?}` | run the rig's time faster (`speed`, default 1×), or only when stepped (`stepped`, for a batch run or a test); refused unless every link is `sim_*`/`fake_*` |
| `extends` | `[path, …]` | this file's own bases, resolved and merged (in order) before this file's own keys are layered on top; the command line's own overlay list still wins |
| `runner` | `RunnerConfig` | how the process serves -- port, who may reach a bare runner (`auth`), how `flyball run`'s front serves it ([`front`](#the-front)), what the API may do, where the store and the directories are; not part of the rig (not in its document or versions; a save over an existing file keeps that file's own section), overridden by the flags of the same names. Every key: [The runner section](../2-config/runner.md) |
| `links` | `{name: Link}` | declared once, referred to by name |
| `devices` | `{name: DeviceEntry}` | the envelope + the driver's own config, [flat or layered](#devices) |
| `controllers` | `{target-address: ControllerEntry}` | keyed by the writable signal driven |

## Several files: overlays

A rig is an ordered list of files, later overlaying earlier — the
docker-compose `-f` / kustomize / Hydra pattern:

```
flyball-runner furnace.yaml sim.yaml
flyball rig check furnace.yaml sim.yaml --set devices.furnace.config.noise=0.3
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
every driver — around the driver's own config, which may sit **flat**
beside the envelope or **layered** under `config:`; both parse to the same
thing (checked at import: a driver's config may not declare a field named
like an envelope key).

| envelope key | type | |
| --- | --- | --- |
| `driver` | string | which driver builds this device; a tag on the process-wide `Config.registry` |
| `label` | string, optional | shown instead of the name |
| `poll_s` | number, optional | inherited down the tree; a namespace or signal override wins |
| `signals` | `{name: SignalOverride \| NamespaceOverride}` | per-signal metadata overrides and access restriction — never adds access the driver did not declare |
| `bound` | `{role: address}` | inputs this device follows on another device: a `role` on the driver's `Input` declarations, resolved to the address's `Signal`/`Node` and read as `self.<input>.value` in `commit` |
| `config` | object | the driver's own settings, if not given flat |

```yaml
devices:
  wet_supply: { driver: sht4x, label: Wet supply, poll_s: 5, link: i2c1, address: 0x46 }   # flat

  hum_sensors:                                                                       # layered
    driver: sht4x_set
    label: Humidity sensors
    poll_s: 1
    config: { link: i2c1, sensors: { chamber: { address: 0x44 }, dry: { address: 0x45 }, wet: { address: 0x46 } } }
    signals:
      chamber: { signals: { humidity: { warn: [20, 80] } } }
      dry:     { poll_s: 5 }
```

(from the plan's worked example — `examples/humidity/rig-multi-sensor.yaml` is the real
file this became).

A `SignalOverride` is `{label, range, precision, warn, alarm, poll_s,
stale_after, limits, max_rate, tags, access, readable, publishing, writable}`:
the first group replaces metadata the driver declared (`tags` are added to the
driver's: `{line: dry}`, a grouping across the tree the UI titles and
filters by; `stale_after` is seconds since the last reading beyond which a
controller regulated from the signal holds its demand rather than apply it;
`max_rate` is `{per_second: N}` (or `per_minute`, `per_hour`, ...), the
fastest a demand may move -- a faster one is clamped to the largest step the
elapsed time allows, not refused); `access` names the set to keep (`"r"`), and
`readable`/`publishing`/`writable` drop one flag each and take only
`false` — the driver declares what it can honour, the file cannot add to
it, unless the driver also names a ceiling for that signal (a Python-level
option, not a rig-file key), in which case `access` may ask for anything up
to and including it. A `NamespaceOverride` is `{label, poll_s, tags, signals}`, recursing
the same way into a namespace's own children; its `tags` apply to every
signal under it, a signal's own winning.

## Links

Every link is a tagged config, declared once under `links:` and referred
to by name from a device's `link` field. The tags and every field, one
section each: [Links](../2-config/links.md); the board tags
(`i2c`, `spi`, `gpio`, `pwm`, `onewire` and their fakes):
[Boards and Linux I/O](../2-config/boards.md).

## Drivers

`driver:` names a tag on the process-wide `Config.registry`; the driver's
own fields sit flat beside the envelope or under `config:`. Every shipped
driver with its fields and an example entry: [Supported drivers](../2-config/devices/drivers.md);
why those fields and where else they appear: [Where a device's options come from](../2-config/devices/generated.md).
Any device entry may say `pin: "LABEL"` (flat, or under `config`) instead
of the link/line fields, when the file has a `board`: the board's fields
for that label fill in, and anything the entry already gives wins.

## Controllers

Keyed by the **target's address** — a controller is named by the writable
signal it drives.

| key | type | |
| --- | --- | --- |
| `signal` | address | the source: a publishing (`P`) signal |
| `law` | `{tag, ...gains}` | e.g. `{tag: PI, kp: 0.2, ki: 0.05}`; omit for none |
| `feedforward` | `{tag, ...}` | maps the source's unit to the target's: `setpoint`, `none`, `affine {gain, bias, rate_gain?}`, `table {points, rate_gain?}`; omit for `setpoint` when the units agree, else `none` |
| `default` | bool | the controller a command means when it names none; at most one per file |
| `min_period_s` | number, optional | step the law at most this often |

```yaml
controllers:
  heaters.heater1: { signal: furnace.zone1, law: { tag: PI, kp: 100, ki: 0.15, tt: 30 } }
  heaters.heater2:
    signal: furnace.zone2
    law: { tag: PI, kp: 100, ki: 0.15, tt: 30 }
    feedforward: { tag: table, rate_gain: 3000, points: [[20, 0], [200, 289.4], [400, 659.8]] }
    default: true
```

(`examples/furnace/rig.yaml`, abridged). `rate_gain` (`affine`,
`table`) adds `rate_gain * rate` to the demand, `rate` being the
setpoint's own rate of change in the source's unit *per second* (zero off
a ramp): target unit per source-unit-per-second — a zone's
`capacity_j_per_k` (J/K = W per °C/s) is the extra power a ramp needs to
charge its own thermal mass. Not on `setpoint`: that feedforward already
hands the target the source's own unit, so a rate term there would be a
lead compensator, a different job from the plant-capacity model this is.

## The front

`runner.front` is read by the Go front `flyball run` starts; `flyballd`
reads the same keys from the top level of `flyballd.yaml`, beside its own.
`flyball-runner` never acts on it, and a block that does not validate is
only a warning to it; `flyball rig check` holds it to this schema. An
unknown key or a wrong type makes the front fall back to the `local` shape
on `127.0.0.1` ([Access](../1-running/runner/access.md#when-a-setting-is-wrong)),
as does every error the table marks *falls back*; a key marked *warns* is
ignored with one warning line and its default used. When `auth` asked for
anything but `local`, or the block cannot be read, a fallback answers `503` on `listen` and serves the
`local` shape on a fresh loopback port (or on `<socket>.local` beside a
`unix:` one) instead.

| key | type | default | |
| --- | --- | --- | --- |
| `listen` | `host:port` or `unix:/path` | `127.0.0.1:8000` (`flyballd`: `127.0.0.1:9000`) | where the front listens; `flyball run --listen` wins. A unix socket file nobody answers on is replaced; one another process answers on is left, and the front does not serve (the rig runs on). Unparseable: falls back |
| `auth` | `local` / `password` / `proxy` / `sso` | `local` | the [shape](../1-running/runner/access.md#shapes-who-gets-in). `local` asked for a non-loopback `listen` falls back unless the run says `--insecure-open`; `sso` falls back (not in this release) |
| `password` | string | none | `auth: password`'s admin password, as the `$scrypt$` line `flyball password` prints; missing or plain text falls back |
| `anonymous` | `none` / `read` | `none` | what a caller with no credential may do under `password` and `proxy`; the `local` shape ignores it. Another value warns |
| `url` | `http(s)://host[:port]` | none | the external address: its host joins the `Host` allow-list (with the loopback names), its origin the `Origin` allow-list; `https` makes the cookie `Secure` and `__Host-flyball`, and the scheme the principal reports. A path, query or user part falls back |
| `tls` | `{cert, key}` | none | PEM files the front serves HTTPS from, TLS 1.2 at least; re-read every 10 s and on `SIGHUP`, the last good pair kept. Unreadable at start: falls back |
| `proxy` | table | none | `auth: proxy`'s identity layer: [below](#proxy-presets). Missing, or one that cannot be vouched for: falls back |
| `session` | duration | `12h` | a session's idle lifetime (`12h`, `2d`); it ends after 7 days whatever. Unparseable warns |
| `trusted_proxies` | `[IP or CIDR, …]` | `[]` | peers whose `X-Forwarded-For` the front believes: when the connection comes from one, the client is the right-most address in that header not in the list. That address is what the sign-in limit counts and the audit and principal record. A bad entry warns and is dropped |
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

A token of `kind: agent`, or one created over plain HTTP from another
machine, lives at most 30 days, or `max_lifetime` if that is shorter. A
lifetime asked for above the cap gets the cap. Durations are Go's (`36h`)
or whole days (`30d`); a bad value warns and its built-in applies. `flyball
token create --config PATH` reads the same block, so a token made offline
gets the same limits.

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
| `secret_file` | a file holding a secret of at least 16 characters, not readable by every user, which the proxy sends as `X-Flyball-Proxy-Secret`; a request without it is not believed, one with a wrong one is refused |
| `issuer`, `audience` | signed presets: the exact `iss`, and a value `aud` must contain |
| `team` | `cloudflare`: the Access team name (one DNS label) |
| `grants` | role → entries. Roles: `all` (every verb) and `viewer` (read); names pending D-034, and an unknown role grants nothing (a warning says so). An entry is a subject, or `group:<id>`. Every grant is on every rig; an identity matching none gets `read` |
| `user_header`, `groups_header`, `separator` | `custom`, unsigned. A header name is letters, digits and `-`; the front's own (`Authorization`, `Cookie`, `Host`, `Origin`, the forwarding headers, `X-Flyball-*`) are refused |
| `jwt` | `custom`, signed: `header`, `jwks_url` (`https`, or `http` on loopback), `issuer`, `audience`, `algorithms` (RS/PS/ES 256–512 and EdDSA; never `none` or `HS*`) |

For a request, the front reads a named token first, then (under
`password`) a session, then the proxy's assertion, and only with none of
them is the caller anonymous. An assertion that is present and wrong -- a
bad signature, a wrong `iss` or `aud`, expired (60 s leeway on `exp`, `nbf`
and `iat`), a second copy of an identity header, a copy under another
spelling (`Remote_User`), groups without a user -- is `401`, never
anonymous. Keys the front cannot fetch make every such request `503`; keys
are refetched after an hour, and for an unknown key id at most once a
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
