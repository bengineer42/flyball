# CLI reference

`flyball` is a standalone Go binary (`daemon/cmd/flyball`) -- build it with
`cd daemon && ./build-with-ui.sh`, which embeds the dashboard its front
serves (a plain `go build ./cmd/flyball` embeds a placeholder; no install
script or release binary yet; see [Installing](../1-running/runner/index.md#installing)). It talks to
a rig over the same HTTP/websocket API any other client uses: through the
front `flyball run` starts, through a `flyballd` daemon in front of several
rigs, or to a bare `flyball-runner` directly.

```
flyball [-s NAME] [--token TOKEN] <command> ...
```

| addressing | | |
| --- | --- | --- |
| `-s`/`--server NAME` | talk to the rig named `NAME`, through a daemon | uses `$FLYBALLD_URL` (default `http://127.0.0.1:9000`) |
| neither given | talk to one rig directly | uses `$FLYBALL_URL` (default `http://127.0.0.1:8000`), with the prefix for a runner started with `--root-path` (`http://host/furnace`) |

With `$FLYBALLD_URL` set and no `-s`, the one rig the daemon runs is
picked; that asks the daemon's management list, so it needs
`$FLYBALLD_TOKEN` with the `manage` scope. Pass `-s NAME` otherwise.

**Credentials.** `--token TOKEN` (or `$FLYBALL_TOKEN`) sends a named token
as `Authorization: Bearer` on every request; without one, the CLI sends the
token `flyball login` saved for that address, if any; without either it
is anonymous. There is no `--url`, `--offline` or schema-caching flag, and
no per-device subcommand tree built from the schema -- those were `cli.py`'s
(the old Python CLI, removed); the Go CLI's device commands are the fixed
`view`/`device-schema`/`invoke` below instead.

## Rig-addressed subcommands

| command | reads/writes | |
| --- | --- | --- |
| `status [--json]` | `GET /api/health` + devices/controllers/activities | devices, controllers, activities, recording, in one screen |
| `schema` | `GET /api/schema` | the whole document |
| `devices` | `GET /api/devices` | name, label, driver, signal tree per device |
| `controllers` | `GET /api/controllers` | every controller's view |
| `demand ADDRESS VALUE` | `PUT /api/signals/ADDRESS` | put a value on a writable signal |
| `read ADDRESS [--fresh]` | `GET /api/read/ADDRESS` | a signal's reading, a namespace's sample, or a device's samples |
| `clock` | `GET /api/clock` | `start_time_ns`, `now_ns`, `elapsed_ns`, `tags`, `speed` |
| `activities` | `GET /api/activities` | what the rig is waiting on |
| `activity fire NAME` | `POST /api/activities/NAME/fire` | settle the activity as met |
| `activity cancel NAME` | `POST /api/activities/NAME/interrupt` | cancel it |
| `watch STREAM` | `/ws/STREAM` | one JSON line per frame; `samples`, `controllers`, `writes`, `signals` |
| `view DEVICE` | `GET /api/devices/DEVICE` | one device's signal tree |
| `device-schema DEVICE` | `GET /api/schema` (the `devices.DEVICE` branch) | one device's config/signal/command schemas |
| `invoke DEVICE COMMAND [KEY=VALUE ...\|JSON]` | `POST /api/devices/DEVICE/commands/COMMAND` | run a device command |
| `sessions` | `GET /api/history/sessions` | recorded sessions, newest first |
| `export SESSION [--format csv\|json\|zip] [--out PATH]` | `GET /api/history/sessions/SESSION/export` | a session as a table; written to `PATH` or stdout |
| `program check\|run\|status\|stop PATH` | `/api/programs/*` | validate, start, watch, stop a program (`run` takes `[--interrupt]`) |
| `sim show\|clock\|step\|set\|reset\|config\|save` | `/api/sim/*` | a simulated rig's knobs |
| `stop [NAME] [--reason TEXT]` | `POST /api/rig/stop` | the [software stop](#stopping-a-rig) |
| `stop RIG-FILE \| --front-dir DIR \| --pid N` | none: `SIGUSR1` | the [software stop](#stopping-a-rig) by signal, for a runner on this host |
| `stop --all [--reason TEXT]` | `GET /api/rigs`, then `POST /api/rig/stop` on each | the software stop on every rig `flyballd` lists for this credential |
| `login [URL] [--scope SCOPE]...` | `POST /api/auth/login`, `POST /api/auth/tokens` | [sign in](#signing-in): the admin password for a saved named token |
| `logout` | | forget the saved token |

`invoke`'s trailing arguments are either `KEY=VALUE` pairs or a single raw
JSON object -- there is no dotted-flag nesting or per-argument `--flag`
(that was schema-driven argparse, `cli.py`-only); `"4"` parses as the
number 4, `"on"` stays a string, matching the old CLI's literal parsing.

### Signing in

`flyball login` works against a front with the `password` shape. It asks
for the admin password on the terminal (never an argument), signs in, has
the front mint a named token, and saves that token -- not the session -- in
`$XDG_CONFIG_HOME/flyball/` (`~/.config/flyball` on Linux), one file per
address, mode `0600`, so a front restart does not sign the CLI out. Every
later command to the same address sends it. `URL` addresses a front
directly; otherwise the usual `-s`/`FLYBALL_URL` addressing applies.

The token is `read` unless `--scope` (repeatable) asks for more, with four
safeguards:

1. anything above `read` prints a warning naming the token file;
2. a bare verb means this rig: `--scope operate` becomes `operate:<rig>`,
   the rig being the `-s NAME`, the path's first segment, or the one rig the
   front says it serves. Every rig on a `flyballd` needs `operate:*`
   spelled out; where the rig cannot be told, a bare verb is refused;
3. a token above `read` lives at most 30 days, less if the front's
   `tokens.max_lifetime` says so;
4. the token is named `cli:<user>@<host>`, so `flyball token list` shows
   which machine holds it.

A token above `read` also gets `read` on the same rigs. `manage` is
refused: only `flyball token create` on the host issues it. `flyball
logout` forgets the saved token on this machine only; the token stays valid
until it expires or `flyball token revoke` removes it. A bare runner has no
password: give the CLI its token (`--token`, `FLYBALL_TOKEN`).

### Stopping a rig

`flyball stop` sends the [software stop](../1-running/runner/access.md#stopping-the-rig)
(program interrupted, every controller in manual, nothing written). How it
reaches the rig depends on how the rig is named (D-042):

| | how | the report |
| --- | --- | --- |
| `flyball stop`, `flyball stop NAME`, `flyball -s NAME stop` | `POST /api/rig/stop` through the front at `$FLYBALL_URL` (or `flyballd` for a `NAME`); needs `operate` | printed |
| `flyball stop --front-dir DIR` | `SIGUSR1` to the runner holding `DIR/runner.lock`; no HTTP call | in the runner's log |
| `flyball stop RIG-FILE` | `SIGUSR1` to the runner holding `runner.lock` in the front-dir `flyball run RIG-FILE` uses (when that is not a temporary directory); no HTTP call | in that run's `run.log`, whose path the CLI prints |
| `flyball stop --pid N` | `SIGUSR1` to `N` as given -- unless `N` is `uv` (`flyball run --uv`, `uv_project:`), which does not pass the signal on: refused, naming the runner under it; no HTTP call | in the runner's log |

An argument with a `/` or a `.yaml`/`.yml` ending is a rig file; anything
else is a rig name. `--front-dir` with `-s NAME`, a `NAME` or a `RIG-FILE`
is a usage error. A signal needs only the OS's own permission (the same
user, or root) -- no front, credential or network -- and cannot reach
another rig that happens to answer at `$FLYBALL_URL`. It carries no
`--reason`: the runner records the stop as `local:signal`.

Over HTTP, when the front answers -- even with a refusal -- that answer
stands. Only a `200` carrying a stop report counts as a stop; a redirect (a
sign-in proxy in front, with no session for the CLI) is not followed and is
refused with the address it pointed to, and any other answer is refused
with its status and body, exit non-zero. When the front cannot be reached,
or has not answered within 5 seconds (each HTTP call a stop makes is
bounded so: connecting, and the whole answer), `flyball stop` exits
non-zero and names the signal forms above; a front that was only slow may
still carry out its stop, and a second stop changes nothing. In the
[D-028 refused state](../1-running/runner/access.md#when-a-setting-is-wrong)
the front's listen address answers `503`, so a plain `flyball stop` cannot
stop the rig there: use `--front-dir` or the rig file.

A `runner.lock` outlives its runner, and the pid in it may since have been
given to another process, so a pid read from one is signalled only while a
runner holds that lock and that pid is known to be the one holding it: on
Linux, `/proc/locks` shows it; on macOS, the process started before the
file was last written, so it is the runner that wrote it. Where neither
can be known, nothing is signalled, and the error names `--pid N` and
Ctrl-C in the `flyball run` terminal instead. A stale lock is refused with
an error naming the pid, and nothing is signalled. While a front rewrites
its front-dir, and until a runner that has just taken the lock writes its
pid, `runner.lock` names no pid: the stop says the runner is starting, and
signals nothing -- try again in a moment. `--pid N` is signalled as given,
except a `uv` process: under `flyball run --uv` or `uv_project:` the
process started (the pid `flyball runners` shows) is `uv`, with the runner
as its child, and `uv` dies of `SIGUSR1` rather than passing it on, so the
stop refuses and names the runner's pid instead. The lock file always
names the runner itself. A `SIGUSR1` that reaches a runner still building
its rig stops nothing (there is nothing to stop yet) and does not end it:
its log says so.

`flyball stop --all` asks `flyballd` (`$FLYBALLD_URL`) for the rigs this
credential holds a verb on (`GET /api/rigs`, no `manage` needed) and stops
each, printing each report under its name. The runner processes stay up.
It exits non-zero if any stop was refused or failed (a redirect, of the
list or of a rig's stop, is not followed and counts as a refusal), or if
the list is empty; with `flyballd` unreachable (or not answering within 5
seconds, the list and each rig's stop alike) nothing is signalled, and each runner
is stopped with `--pid` or `--front-dir`.

## Local (no rig or daemon involved)

| command | | |
| --- | --- | --- |
| `rig check FILE... [--set KEY=VALUE] [--print]` | validate one or more rig files (later overlays earlier) against the embedded rig schema and the same hand-written cross-field rules `RigConfig` enforces; prints a one-line summary, and the merged document with `--print`. The schema holds the drivers and links of every first-party package (the engine, `flyball-sim`, `-modbus`, `-visa`, `-chips`, `-linux`, `-qcodes`, `-pymeasure`, and `examples/furnace`), not those of a package of your own; and a `board:` profile is not applied, so a device that names a `pin:` is refused here though the runner accepts it |
| `rig schema` | the rig file's JSON Schema, for an editor (`# yaml-language-server: $schema=`) |
| `program schema` | the program file's JSON Schema |
| `run RIG-FILE [--listen ADDR] [--uv] [--insecure-open] [flyball-runner flags...]` | [start a rig](#flyball-run) behind a front, in the foreground |
| `password [PASSWORD]` | hash a password for `runner.front.password` (or `password:` in `flyballd.yaml`); prompts if omitted |
| `token create --name N --config PATH [--config PATH]... [--set KEY=VALUE]... [--daemon] [--scope S]... [--kind human\|service\|agent] [--expires D]` | [make a named token](#named-tokens) in the front's tokens file; prints it once |
| `token list --config PATH [--daemon]` | the tokens in that file: id, name, scopes, kind, created, expires, last used -- never a secret |
| `token revoke ID --config PATH [--daemon]` | remove one; the front stops accepting it within a second |
| `new NAME [--dir PATH]` | write `NAME.py`: a complete device driver with a tag, ready to edit |

!!! note "`rig check --print`'s formatting"
    The Go CLI's `--print` prints the same merged document as the old
    Python tool, but reordered (keys alphabetical, not the model's
    declared order) and reformatted (a whole-number float prints without
    `.0`; a schema-defaulted field like `recording: false` that isn't in
    the input files doesn't appear). Same data, not byte-identical output.

`program check` always validates against a running rig
(`POST /api/programs/check`, the rig-addressed table above); there is
no local, offline-against-installed-commands mode as `cli.py` had.

### `flyball run`

`flyball run RIG-FILE [RIG-FILE…]` starts the rig's front and runs `flyball-runner`
behind it, in the foreground, no daemon involved. The front serves the
dashboard and passes `/api`, `/ws` and `/mcp` to the runner, which listens
only on a socket in its [front-dir](../6-internals/front.md#the-front-dir).
What the front serves comes from
[`runner.front`](../2-config/runner.md#front-how-flyball-run-serves-the-rig)
in the rig as the runner builds it: every leading rig file, later
overlaying earlier, `extends` resolved, then every `--set KEY=VALUE`
([several files](../2-config/index.md#several-files); D-046). So
`flyball run rig.yaml sim.yaml --set runner.front.anonymous=none` serves
what the runner reads, and `--set name=x` names the front's rig `x` too.
The rig files are the arguments before the first flag; `--` and an
argument shaped like a negative number (`-1`, `-.5`), which the runner
would take as rig files, are refused. The first file names the front's
state and front-dir. The front prints where it serves:

```
flyball: serving rig oven on http://127.0.0.1:8000/ (local)
```

| flag | | |
| --- | --- | --- |
| `--listen ADDR` | `runner.front.listen` | where the front listens: `host:port` or `unix:/path`; default `127.0.0.1:8000`. `--serve-ui ADDR` is the same flag's old name |
| `--uv` | `runner.front.uv` | run `flyball-runner` via `uv run --project <the rig file's directory>`, for an application that keeps it in its own venv (`examples/humidity`, `examples/furnace`). a SIGINT or SIGTERM to `flyball run` goes on to the runner once |
| `--insecure-open` | `FLYBALL_INSECURE_OPEN=1` | serve the `local` shape (no sign-in) on a non-loopback `listen`, for this run only; there is no file key. It then answers only an IP address, a loopback name, the machine's own name or `url`'s host, on every route ([DNS rebinding](../1-running/runner/access.md#when-a-setting-is-wrong)) |

Every other argument goes to `flyball-runner`. A flag beats the file.
`runner.run` (`serve_ui`, `uv`) is still read for one release with a
warning; `--port` and `runner.run.port` mean nothing now.

The front gives a client 10 s to send its headers, closes a keep-alive
connection after 120 s idle, and refuses headers over 64 KiB (`431`); a
websocket or a download is not cut short. A page opened before the runner
answers gets `503` with `Retry-After: 1`, and the dashboard says
*starting…* until it does.

A runner that crashes, or cannot serve (exit 5), is started again with a
fresh key (1 s backoff, doubling to 30 s, back to 1 s after 10 s up). The
run ends when the runner exits cleanly, or with exit 2 (a bad rig file, or a
`flyball-runner` too old for `--front-dir`), 3 (another runner holds the
rig), or 4 twice (its front-dir refused): `flyball` then exits 1, naming
it ([exit codes](#exit-codes)). Ctrl-C or SIGTERM stops the runner and ends the run,
and says what the next presses do (D-045): a second Ctrl-C (or SIGTERM)
sends the runner SIGINT again, which makes it cut its shutdown short, and a
third kills its process group (SIGKILL). `flyball` exits only once the
runner has, so no press leaves it running. Only the first gives the runner
its whole shutdown; after the second or third, the recording may not be
closed cleanly. A run whose runner was killed rather than stopped (the
third press, or any signal but the stop's own SIGINT or SIGTERM) exits
128+N for signal N -- 137 for the third press's SIGKILL -- not 0, so a
wrapper can tell a forced kill from a clean stop. A dropped terminal or SSH session
does not (D-038): the front and the runner ignore the hangup and keep the
rig running, their output also goes to `run.log` in the front's state
directory (below; mode 0600, rotated once to `run.log.1` past 4 MiB), and a
notice at start says so. If the rig is already running, `flyball run` refuses
and names the runner's pid: end that run first (Ctrl-C in its terminal, or
`kill <pid>`) -- `flyball stop` stops the rig, not the runner. For a rig that should survive a reboot, use
[`flyballd`](#the-daemon) under systemd. A front that cannot listen does not
stop the rig: it says so, and the rig runs on, stoppable by signal or
`flyball stop`.

The front keeps its named tokens and its audit in
`$XDG_STATE_HOME/flyball/front-<id>/` (`~/.local/state/…`), the id derived
from the rig file's absolute path, so each rig file has its own. With
neither `XDG_STATE_HOME` nor `HOME` set (a systemd unit with no `User=`, a
minimal init script), `~` is the user's home from the password database,
such as `/root`. Only if that cannot be looked up, or is not a directory
the user owns and no one else can write (`/`, anything under `$TMPDIR`),
does `flyball run` (and `flyball token --config RIG-FILE`) refuse rather
than use a shared directory.

### Named tokens

`flyball token create|list|revoke --config PATH` work offline on the file a
front reads its named tokens from, under a lock, so they are safe while the
front runs; it notices a change at its next check. `create` takes
`--config` more than once and `--set KEY=VALUE`, merged as `flyball run
PATH PATH… --set …` merges them, so `runner.front.tokens` limits the token
as that front would; the tokens file is the first `PATH`'s (a
`flyballd.yaml` takes one `--config` and no `--set`). `PATH` is the
front's config:

- a rig file: the tokens of `flyball run PATH`,
  `$XDG_STATE_HOME/flyball/front-<id>/tokens.json`;
- a `flyballd.yaml`: `<data_dir>/front/tokens.json`. It is recognised by
  its name (`flyballd.yaml` or `flyballd.yml`), or by having one of
  `manifests_dir`, `data_dir`, `default_server` or `log_max_size` at its
  top level. A daemon config under another name that sets none of them
  -- only the front's keys -- would be taken for a rig file: say
  `--daemon`.

`create` prints the token alone on stdout and its details on stderr, so
`flyball token create … > token.txt` keeps just the token. `create` and
`revoke` are recorded in the front's audit beside the tokens file
(`audit.jsonl`, by `local:cli`); a token whose record cannot be written is
not created, and a revoke that cannot be recorded still happens and exits
non-zero ([what is recorded](../1-running/runner/access.md#what-is-recorded)).

Run as root (`sudo flyball token …`) against a front whose state directory
belongs to another user -- or, if it does not exist yet, whose nearest
existing parent does, or whose `tokens.json` or `audit.jsonl` does -- a
token command refuses and names `sudo -u <owner>`: a file it made there
would be root's, and that front could no longer read its tokens or write
its audit, and would refuse sign-ins.

| flag | default | |
| --- | --- | --- |
| `--name N` | required | 1 to 64 characters, no control characters |
| `--daemon` | off | `PATH` is a `flyballd.yaml`, whatever its name and keys (`create`, `list` and `revoke`) |
| `--scope S` | `read` | repeatable: `read`, `operate` (every rig), `operate:<rig>`, `read:<rig>`, `manage` (`flyballd`'s management routes; only here). A scope above `read` also gets `read` on the same rigs. The verbs are pending D-034 |
| `--kind K` | `service` | `human`, `service` or `agent`; an `agent` token lives at most 30 days |
| `--expires D` | the front's `tokens.default_lifetime` (90 days) | `30d`, `36h`; capped at `tokens.max_lifetime` (365 days at most) |

## The daemon

`flyballd` supervises one `flyball-runner` per manifest and serves them all
behind one front, each rig under its `root_path`; `/` lists them.

```
flyballd [-config flyballd.yaml] [-insecure-open]
```

`-insecure-open` (or `FLYBALL_INSECURE_OPEN=1`) serves the `local` shape on
a non-loopback `listen`, for that run only, by an IP address, a loopback
name, the machine's own name or `url`'s host. `flyballd.yaml`'s front keys --
`listen`, `auth`, `password`, `anonymous`, `url`, `tls`, `proxy`, `session`,
`trusted_proxies`, `tokens` -- sit at its top level beside the daemon's own,
and mean what they do in [`runner.front`](rig-file.md#the-front); the
getting-started ones are in [Access](../1-running/runner/access.md). A
front key that cannot be read makes the front fall back to the `local`
shape on loopback, with a warning; the rigs run regardless. When `auth`
asked for `password` or `proxy`, or the front keys cannot be read, that fallback answers `503` on `listen`
and serves the `local` shape on a fresh loopback port, named on the
`flyballd listening on` line.

| key | default | |
| --- | --- | --- |
| `listen` | `127.0.0.1:9000` | where the front serves. A client has 10 s to send its request headers, in at most 64 KiB, and an idle keep-alive connection is closed after 120 s; a response has no time limit, so log streaming and websockets stay open |
| `manifests_dir` | `manifests` | one `NAME.yaml` per rig (below) |
| `data_dir` | `data` | captured runner logs under `logs/` (`NAME.log`, and `NAME.log.1` once it has been capped; `logs/` is `0700` and each file `0600`), and the front's `front/tokens.json` and `front/audit.jsonl` |
| `log_max_size` | 10 MiB (`10485760`, in bytes) | per-runner captured-log cap; `0` for none. Checked every 2 s: past it, `NAME.log` is copied to `NAME.log.1` (replacing the last one) and emptied, so a runner's logs take at most about twice the cap. The runner keeps writing to the same file, so a daemon crash does not cut its output; a line written at the moment of the copy can be lost |
| `default_server` | none | read, not yet used |

`auth: {token, insecure_open}` from before is refused as a whole: the front
falls back to the `local` shape on loopback and says why. Management now
takes a token with the `manage` scope, and `--insecure-open` is a flag.

A manifest:

| key | default | |
| --- | --- | --- |
| `name` | required | lower-case letters, digits, `-` and `_`, up to 64: it names the log file, the URL prefix, the rig in a scope (`operate:NAME`) and the runner's audience |
| `server_config` | required | the rig file |
| `root_path` | `/NAME` | `/segments` of the same characters. One that contains another rig's, or is under `/api`, is refused |
| `restart` | `on-failure` | below |
| `network` | `unix` (`tcp` on Windows) | how the front reaches the runner: a socket in its front-dir, or loopback TCP. `tcp` is Windows only until the runner proves it holds the key (D-044): elsewhere a manifest that says `network: tcp` still starts its rig, on the unix socket in its front-dir, and `flyballd` logs a warning saying so. flyball does not run on Windows yet; a port is planned |
| `host`, `port` | `127.0.0.1`, none | `network: tcp` on Windows only, and `port` is required there; `host` must be loopback. Any local user can connect to that port; `flyballd` logs a warning |
| `enabled` | `true` | `false`: not started |
| `uv_project` | none | a directory to `uv run --project` `flyball-runner` from, when it isn't on `flyballd`'s own `$PATH` |

A manifest that says otherwise is refused, at start-up or over the API
(400; 409 for a name or root path already taken). A manifest has no
`anonymous`: what a caller with no credential may do is set once, for
every rig, by `anonymous` in `flyballd.yaml`, and a manifest that sets it
is refused with that said.

### What stopping `flyballd` does

**`flyballd` never stops its runners by stopping itself** (D-037): a
SIGTERM, a SIGINT, `systemctl restart` or a crash ends `flyballd` alone, and
every rig keeps running as it was -- programs, controllers, recording. A
restart or failure of the management plane never changes what the
hardware does. While `flyballd` is down its rigs cannot be reached over
HTTP, but [`flyball stop --front-dir DIR`](#stopping-a-rig) still stops one
by signal.

A starting `flyballd` **adopts** each runner still alive in its
front-dir: it checks it with the signed handshake and routes to it again,
without a restart -- same process, same key. `GET /api/runners` then shows
it `running` with `adopted: true` and its `pid`. Adoption needs the
front-dirs to be where the last `flyballd` left them: under systemd,
`/run/flyball/<name>` kept across restarts (the unit below); otherwise
`$XDG_RUNTIME_DIR/flyball/<id>/<name>`, the id derived from the path of
`flyballd.yaml`. With no private runtime directory `flyballd` warns at
start that its runners get temporary front-dirs and will not be adopted;
it warns for one runner when the runtime directory is too deep for that
runner's socket path. Such a runner, left running by a `flyballd` that
stopped, keeps the rig: the next `flyballd`'s runner for it exits 3 and
the rig is `busy` until the old runner is ended (`kill <pid>`, the pid in
its `<store>.lock`).
An adopted runner is not `flyballd`'s child: when it exits, its exit
status cannot be known, and the manifest's `restart` policy treats it as a
crash.

To end the runner processes -- for maintenance, or before an uninstall --
`flyball runners stop NAME` or `flyball runners stop --all`, with a
`manage` token. That is not the software stop: `flyball stop --all`
interrupts every rig and leaves its runner up.

An example unit is `daemon/deploy/flyballd.service`. Two of its settings
are what let runners outlive `flyballd`, and neither may be dropped:
`KillMode=process` (systemd would otherwise kill every process in the
unit's cgroup, runners included, on `stop` or `restart`), and
`RuntimeDirectory=flyball` with `RuntimeDirectoryMode=0700` and
`RuntimeDirectoryPreserve=yes` (systemd would otherwise delete
`/run/flyball` when `flyballd` stops, from under the runners using it).

```
sudo cp daemon/deploy/flyballd.service /etc/systemd/system/   # then edit User=, the paths, PATH
sudo systemctl daemon-reload && sudo systemctl enable --now flyballd
```

### A runner's status

`GET /api/runners` gives each rig's `name`, `root_path`, `restart`,
`status`, `endpoint` (`unix:/run/flyball/NAME/sock`), `pid` (0 when none),
`adopted` and `reason` (why it is `busy` or `failed`). The status follows
the process:

| status | |
| --- | --- |
| `starting` | spawned, or found in its front-dir, and not yet through the signed readiness handshake (probed every 0.5 s) |
| `running` | through it |
| `restarting` | it exited, its `restart` policy restarts it, and it is waiting out the backoff: 1 s, doubling to 30 s, back to 1 s once a restart reaches `running` |
| `stopped` | it exited cleanly on its own, and its `restart` policy says not to restart it |
| `failed` | it crashed and its `restart` policy says not to restart it, it exited 2 (a bad rig file), it refused its front-dir twice (exit 4), or it could not be started again |
| `busy` | another runner already holds the rig (exit 3), or a runner holds its front-dir that `flyballd` cannot adopt; never restarted |

A manifest's `restart` says what happens when a runner exits on its own:

| `restart` | a crash (non-zero exit) | a clean exit (0) |
| --- | --- | --- |
| `on-failure` (default) | restarted, after the backoff | `stopped` |
| `always` | restarted, after the backoff | restarted, after the backoff |
| `never` | `failed` | `stopped` |

Exit code 2 is never restarted, under any policy: it is `flyball-runner`'s
code for a rig file that does not validate or a rig that cannot be built
(and `uv`'s, for a project or command it cannot find), which a restart
cannot fix. The runner is `failed` until `daemon restart`. Every
incarnation gets a fresh key.

`daemon restart` restarts a runner at once, whatever its exit code, and
cuts short a crash backoff; a `stopped` or `failed` runner starts again. It
sends `SIGTERM` and returns; a runner still there after 10 s is `SIGKILL`ed
and the new one started all the same. `daemon stop` (and `runners stop`)
sends `SIGTERM`, `SIGKILL`s a runner still there after 10 s, and returns
once it is gone -- also for a runner in backoff, which does not come back.
The `SIGKILL` goes to the runner's process group, so it reaches the runner
under `uv` (`uv_project:`) too. An adopted runner is signalled by its pid.

### Management

The management routes -- the runner list, `/`, starting, stopping,
restarting, logs -- need a named token carrying the `manage` scope, sent as
a bearer token: `flyball token create --name ops --scope manage --config
flyballd.yaml`. Without a credential they answer `401`; with any other --
a signed-in session, the `local` shape, a proxy identity, a token without
`manage` -- `403`. `GET /api/rigs` is not management: it lists the rigs
the caller holds any verb on (`[{name, root_path, status}]`). Each rig
under its `root_path` is reached through the front like any other, with
the caller's own credential.

| command | | |
| --- | --- | --- |
| `daemon runners` | `GET /api/runners` | list registered runners and their status |
| `daemon start MANIFEST.json` | `POST /api/runners` | register and start one |
| `daemon stop NAME` | `DELETE /api/runners/NAME` | stop and deregister it |
| `daemon restart NAME` | `POST /api/runners/NAME/restart` | restart it |
| `logs NAME` | `GET /api/runners/NAME/logs` | its captured stdout/stderr (`NAME.log`; not the capped-off `NAME.log.1`) |
| `runners stop NAME \| --all` | `GET /api/runners`, `DELETE /api/runners/NAME` | end runner processes; says for each whether it was stopped, or was `busy` and left alone |

`daemon …` and `logs` send `$FLYBALLD_TOKEN`; `runners stop` sends
`--token`, else `$FLYBALLD_TOKEN`.

## Exit codes

One table for `flyball` and `flyball-runner`: a number means the same
thing wherever it appears. `flyballd` reads the runner's codes as its
[runner status](#a-runners-status) says; `flyball run` ends with 1 on the
runner's 2, 3 or a second 4, naming it, and starts the runner again on
any other failure.

| code | `flyball` | `flyball-runner` |
| --- | --- | --- |
| 0 | done | stopped cleanly (Ctrl-C, SIGTERM, a shutdown asked over the API) |
| 1 | the rig, the daemon, or a local check refused; the message is theirs | an unexpected error (a traceback) |
| 2 | no command given | the rig file does not load, the rig cannot be built, or a flag is unknown: one line on stderr; starting again will not help |
| 3 | -- | the rig is busy: another runner holds its `<store>.lock`, or the front-dir's `runner.lock` (the message names it) |
| 4 | -- | `--front-dir` is unsafe or incomplete, found before the rig's lock is taken or any hardware touched; the front writes it again |
| 5 | -- | it could not serve: its socket or port could not be bound, or its server did not start (uvicorn's own exit 3, said as what it is). Not busy: starting again may work |
| 128+N | `flyball run`: its runner was killed by signal N rather than stopped (137: the third Ctrl-C's SIGKILL); its recording may not be closed cleanly | -- |
