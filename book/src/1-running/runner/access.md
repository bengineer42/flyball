# Access: the front, sign-in, stopping

Who may reach a rig, how they prove it, and how anyone allowed to can stop
it. People and machines reach a rig through the **front**: the Go server
that `flyball run` and `flyballd` start in front of each runner. A runner
started on its own, with no front, keeps a small door of its own: a token,
or nothing.

## Three ways a rig is served

| started with | the door | for |
| --- | --- | --- |
| `flyball run rig.yaml` | a front, on `runner.front.listen` (default `127.0.0.1:8000`), serving the dashboard and passing `/api`, `/ws` and `/mcp` to the runner | one rig: a laptop, a Pi |
| `flyballd` | one front for every rig it supervises, each under its root path (default `127.0.0.1:9000`) | several rigs on one machine ([the daemon](../../7-reference/cli.md#the-daemon)) |
| `flyball-runner rig.yaml` alone | the runner's own: a token, or nothing ([the bare runner](#the-bare-runner)) | a container, a script, a public demo |

Behind a front the runner listens only on a unix socket in a directory
nobody else can enter, and it serves a request only if the front signed
it: a short-lived **principal** naming the caller and the verbs it holds on
this rig ([The front and the runner](../../6-internals/front.md)). Sign-in,
sessions, tokens, the `Host` and `Origin` checks and TLS are all the
front's; the runner's own `runner.auth`, `--token` and `--anonymous` are
ignored there, with one line on stderr saying so.

## Shapes: who gets in

One key, `auth`, picks the front's **shape**:

| shape | who gets in | for |
| --- | --- | --- |
| `local` (the default) | anyone who reaches it, with every verb; no sign-in. Loopback only | a laptop, or a Pi with a screen |
| `password` | a person with the admin password (a session in the browser); a machine with a named token; anyone else as `anonymous` says | a lab network |
| `proxy` | whoever an identity proxy in front vouches for, per its preset; named tokens; anyone else as `anonymous` says | behind Tailscale Serve, Authelia, oauth2-proxy, authentik, Pomerium or Cloudflare Access |

`sso` is reserved for a later release: today it falls back (see
[below](#when-a-setting-is-wrong)), with a pointer to `proxy` and the
oauth2-proxy preset.

With nothing configured, `flyball run rig.yaml` serves the `local` shape
on `http://127.0.0.1:8000/`: the dashboard, no sign-in, nothing to set up.
The `local` shape trusts every process of every user on the machine, so it
is for a machine one person uses. It answers only to the names
`localhost`, `127.0.0.1` and `[::1]` (on any port), so a web page that
points its own name at your machine (DNS rebinding) is refused.

On a lab network, the password shape:

```yaml
runner:
  front:
    listen: 0.0.0.0:8000
    auth: password
    password: $scrypt$n=16384,r=8,p=1$…   # the line `flyball password` prints
```

The password must be the hashed line `flyball password` prints; a plain
one is refused. `anonymous: read` lets anyone who reaches the rig watch it
without signing in (every `GET` and every stream); the default is `none`.
A dashboard holds four sockets open; the front allows 128 open requests
per rig to callers with no credential -- sockets, streams and every other
request -- and 512 held sockets and streams in all, so a crowd of viewers
cannot shut a signed-in operator out, and the stop is never
counted ([refusals](../../4-server/api.md#authentication)). A request body
must arrive within two minutes of the request.
Most sites need two more keys at most: `url` and `tls` (next). Every other
key -- session length, token lifetimes, forwarded addresses, the proxy
presets' details -- is in the [reference](../../7-reference/rig-file.md#the-front).
`flyballd` reads the same keys from the top level of `flyballd.yaml`.

## `url:` and `tls:`

**`url`** is the address people use to reach the rig
(`https://pi.lab.example`), when it is not simply the address the front
listens on -- a host name, or a TLS proxy in front. It adds that host to the
names the front answers to and its origin to the pages that may act, and
when it is `https` the session cookie is marked `Secure` and named
`__Host-flyball`, also when TLS ends at the proxy. Without it, a password
or proxy front answers any `Host`, and a request that acts must come from a
page on the same site as that `Host` -- except to a caller with no
credential: `anonymous: read` is served the rig only by an IP address,
a loopback name or the machine's own name (`hostname`, and
`<hostname>.local`), never by another DNS name, which a web page elsewhere
could point at the front (DNS rebinding). Signing in and the dashboard's
own files answer any name, and a session or a token is served by any
name, so by another name the dashboard shows its sign-in page (`/api/auth`
reports no verb there, and a socket is closed `4401`). To let anonymous
viewers in by a site DNS name, set `url` to it.

**`tls: {cert, key}`** has the front serve HTTPS itself from a certificate
and key file (PEM), TLS 1.2 at least. It re-reads both files at most every
10 s, and at once on `SIGHUP`, so a renewed certificate is picked up with no
restart; a renewal it cannot read leaves the last good pair in use. flyball
obtains no certificate itself: `tailscale cert`, an ACME client using the
DNS challenge, or a site CA make one. A self-signed certificate still
encrypts the password and the cookie, but browsers warn about it. On a
Raspberry Pi prefer an ECDSA certificate to RSA: the handshake is cheaper.
Leave `tls` out when nginx, Caddy or Tailscale in front terminates TLS, and
set `url` instead.

A password or proxy front listening beyond loopback without `tls` prints a
warning at start: the password, tokens and cookies cross the network in the
clear.

## When a setting is wrong

A wrong setting in the front never stops the rig: it narrows who can reach
it. The front serves the `local` shape -- no sign-in, every verb -- on
loopback instead, and says why.

When the setting that is wrong belongs to a shape with credentials -- a
shape that is not one of the three, `sso`, a password that is missing or
not a `$scrypt$` line, TLS files that cannot be read, a `url` that does not
parse, or a `proxy` block that cannot be vouched for -- or when the
`runner.front` block fails validation or cannot be read because the rig file's
`extends` cannot be resolved, so its shape is unknown, the address it was
asked to listen on answers every request `503` (over TLS when `tls:` names a
certificate and key that load, so a browser or an `https` upstream can read
it; in plain HTTP when they do not), and the
`local` shape is served somewhere else: a fresh port on `127.0.0.1`, or a
socket beside a `unix:` one (`front.sock.local` next to `front.sock`). A
reverse proxy on the same machine keeps forwarding to the address it was
given, and must not find an open console there:

```
flyball: front: the password is not a $scrypt$ line (…); plaintext passwords are refused -- `flyball password` makes one -- 127.0.0.1:8000 answers 503 (auth misconfigured), and the local shape is served on 127.0.0.1:0 only (a fresh port: the line saying where it serves names it); the rig keeps running (D-028)
flyball: serving rig furnace on http://127.0.0.1:40321/ (local)
```

A plain `flyball stop` goes to that address too, over HTTP, and gets the
`503`, so the `503` starts with the stops that work, all signals: Ctrl-C in the `flyball run`
terminal, `flyball stop --front-dir DIR` or `flyball stop --pid N` on the
rig's host. Under `flyballd`, `DIR` is the rig's front-dir,
`/run/flyball/NAME` with the systemd unit, and the stop runs as `flyballd`'s
user. Stopping `flyballd` itself is not a stop: it leaves its runners, and
their rigs, running ([D-037](../../7-reference/cli.md#what-stopping-flyballd-does)); `flyball runners
stop NAME`, with a `manage` token and `FLYBALLD_URL` set to the local
address `flyballd` logs, ends a runner. The `503` then says only that
authentication is misconfigured and where the reason is -- the `flyball
run` terminal and its `run.log`, or `journalctl -u flyballd` -- since
whoever the proxy lets reach that address may be anyone, and a reason
can quote the file. `flyball run`'s start notice
then names `flyball stop --front-dir` with its own front-dir.

A `listen` that does not parse serves the `local` shape on
`127.0.0.1:8000`, with nothing to refuse.

The reason is in `GET /api/auth` (`exposure.warning`, with `exposure.port`
the console's), and a `fallback` record goes to the front's
[audit](#what-is-recorded). The `local` shape asked for an address beyond
loopback falls back to `127.0.0.1` on the same port. To serve it on the network anyway, say so for that run:
`--insecure-open`, or `FLYBALL_INSECURE_OPEN=1` in the environment of
`flyball run` or `flyballd`. There is no file key for it: a file can be
copied from anywhere, and `extends:` would pass it on. The front then
answers, on every route (signing in and making tokens included), only an
IP address, a loopback name, the machine's own name (`hostname`, and
`<hostname>.local`) or `url`'s host -- never another DNS name, which a web
page elsewhere could point at it (DNS rebinding); anything else is `403`,
naming the names it takes. It warns at every start, and the dashboard shows
a banner that cannot be dismissed. Not on a rig a model can drive.

## Signing in, sessions and tokens

**People**, at a `password` front, sign in at the dashboard's login page
with the admin password. **Options › Access** (the gear in the app bar,
`#/options/access`) says who this browser is on the rig and which verbs it
holds, and has the Sign in / Sign out button. The front keeps the session in memory and gives
the browser an `HttpOnly` cookie (`flyball-<port>`, or `__Host-flyball`
under HTTPS); the page keeps no secret. A session ends after 12 hours
without a request from it (`session:` changes that), 7 days at most, at
**Sign out**, or when the front restarts. An open, running dashboard polls
the rig every few seconds, and each poll counts, so while a tab is open
the session lasts to its 7 days whatever `session:` says; it idles out
only once the tab is closed, or frozen by the browser or a sleeping
laptop. A wrong password is refused after half a second; ten wrong
in a minute from one address are refused until the oldest is a minute
old. There is one admin password and no user accounts, so the record of an
action says `local:admin` and a session id, not a person; per-person
records come with the `proxy` shape.

**Machines** -- scripts, the CLI, MCP clients -- send a **named token**:
`Authorization: Bearer fbt1_…`. A token is made on the rig's host, before
or while the front runs, into the file the front reads:

```
flyball token create --name ci --config rig.yaml                   # read only, 90 days
flyball token create --name bench --config rig.yaml --scope operate --expires 30d
```

It prints the token once; the file keeps only its hash. Every token
expires: after 90 days unless it says otherwise, a year at most, and 30
days at most for `--kind agent` or a token made over plain HTTP from
another machine -- plain HTTP on the hop to the front itself, whatever
`url` says, so also through a TLS proxy on another host. `flyball token list` and `flyball token revoke ID` work on
the same file; a revoked or expired token's open streams and sockets are
closed within a second. Revoking a token never changes what the hardware is
doing: a program it started keeps running. The admin session (or anyone
at a `local` front) can do the same from the browser side:
`POST /api/auth/tokens`, `GET`, `DELETE /api/auth/tokens/{id}`
([the API](../../4-server/api.md#authentication)); under the `proxy` shape
tokens are made with `flyball token create` only.

`flyball login` trades the admin password for a named token of its own,
saved for the CLI (`read` unless `--scope` asks for more): [the CLI
reference](../../7-reference/cli.md#signing-in).

**What a token may do** is its scopes: `read` or `operate`, on every rig
(`operate`) or one (`operate:furnace`), and `manage` for `flyballd`'s own
routes. `read` is every `GET` and every stream, plus checking a rig or a
program file; `operate` is everything else, stopping the rig included.
Neither includes the other: a token holding `operate` alone can stop the
rig but gets `403` from `flyball status` or any stream, even where
anonymous callers may read. `flyball token create --scope operate` and
`flyball login --scope operate` add `read` on the same rigs for you (the
`bench` token above holds `operate:*` and `read:*`); a token made through
`POST /api/auth/tokens` holds exactly the scopes it asks for, so ask for
`["read", "operate"]` there. These two verbs are a placeholder: which verbs there are, and what each
route needs, is still being decided (D-034, pending), so expect the list
to change.

## Behind an identity proxy

The `proxy` shape puts the rig behind a login the site already has. One
preset line says which, and the front reads the identity the proxy
asserts -- a signed token, or plain headers from a proxy it can vouch for.

Authelia (or oauth2-proxy, or authentik, unsigned), with the proxy talking
to the front over a unix socket:

```yaml
runner:
  front:
    listen: unix:/run/flyball/front.sock
    auth: proxy
    proxy:
      preset: authelia
      grants: {all: ["group:lab-admins"]}
```

The front believes whoever can connect to that socket, so the file
permissions are what keep another local process from posing as the proxy.
The front makes the socket `0660` (its own user and group, never everyone)
and will not listen in a directory anyone may write to without the sticky
bit, or in one of its own that others may enter. A web server proxy runs
as another user: make it a user nothing else runs as -- not `www-data`,
which PHP-FPM, Pi-hole and other web apps on the same machine often share --
here `flyball-proxy`. Give the directory to the front's user and the proxy's
group, setgid so the socket inherits that group, and put nobody else in the
group (`tailscaled` runs as root and needs no group):

```sh
sudo install -d -o flyball -g flyball-proxy -m 2750 /run/flyball
```

A `systemd` unit can do the same with `RuntimeDirectory=flyball`,
`RuntimeDirectoryMode=2750` and `Group=flyball-proxy`. Every process of that
group, and of the proxy's user, can assert any identity; the audit records
the local user behind each new one (`proxy.peer`), after the fact. When a
setting is wrong ([above](#when-a-setting-is-wrong)), the `local` shape is
served on `front.sock.local`, beside the socket and with its group, so that
group reaches the local console -- every verb, no sign-in -- until the
setting is fixed.

`secret_file` works with `from: unix` too: the front then believes a
connection only when it also sends the secret as `X-Flyball-Proxy-Secret`,
so where the proxy's user cannot be its own, keep the header in a
configuration file only root reads (nginx reads its configuration as root:
a `0640 root:root` include with `proxy_set_header X-Flyball-Proxy-Secret
"…";`), and other processes of that user cannot pose as the proxy by
connecting alone. It is a second layer, not the separation: a process can
often read the memory of another of the same user, the proxy's included.
A user of the proxy's own is the separation.

Tailscale Serve is the same with `preset: tailscale` and
`tailscale serve unix:/run/flyball/front.sock` on the Tailscale side
(Tailscale documents a unix-socket target; not tried against a live
tailnet here). A tagged device and a Funnel visitor carry no Tailscale
identity, so they are anonymous.

`grants` says who may do what: a role, `all` (every verb) or `viewer`
(read), mapped to user names and `group:<name>`s as the proxy sends them.
Anyone the proxy vouches for who matches nothing gets `read` on every rig.
The role names are pending D-034 with the verbs. A user is known by the
proxy's own stable id, never by its e-mail address, which grants
nothing. Named tokens work under the proxy shape too, so a proxy never
needs to wave some paths through unauthenticated for machines.

Every preset, what each needs and what is not yet tried against the real
product: [the proxy presets](../../7-reference/rig-file.md#proxy-presets).

## Other names, other pages

For every request the front refuses, before anything else:

- a path with a `.` or `..` segment, a backslash, or an encoded `.`, `/` or
  `\` (`400`);
- a `Host` it does not answer to (`403`): at the `local` shape, anything but
  a loopback name; with `url`, anything but that host or a loopback name;
  at the `local` shape served beyond loopback by `--insecure-open`, anything
  but an IP address, a loopback name, the machine's own name or `url`'s
  host;
- on the rig's `/api`, `/ws` and `/mcp`, a caller with no credential
  (`anonymous: read`) whose `Host` is not an IP address, a loopback name,
  the machine's own name or `url`'s host (`403`); a session or a token
  passes any `Host`;
- a request that acts -- any method but `GET`, `HEAD` and `OPTIONS`, and
  every websocket -- whose `Origin` is missing, `null`, or not the same site
  as its `Host` (or `url`'s origin) (`403`). A request with no `Origin` passes
  only if the browser says `Sec-Fetch-Site: same-origin`. A request with a
  named token is exempt: another site's page cannot have one.

Behind nginx or another proxy, pass the browser's `Host` through (nginx:
`proxy_set_header Host $http_host;`), and set `url` when the proxy
terminates TLS. The front takes the client's address from the connection,
so behind a proxy every client shares the proxy's address for the
sign-in limit; `trusted_proxies` in the
[reference](../../7-reference/rig-file.md#the-front) names proxies whose
`X-Forwarded-For` it may believe.

An `Authorization` header that is not a flyball token is refused (`401`)
at the `local` and `password` shapes, session cookie or not; an
`Authorization: Basic` one says so in its `401`. nginx forwards the
browser's `Basic` header after its own `auth_basic`, so two setups work:

- **nginx is the gate.** The `proxy` shape with the `custom` preset takes
  the user nginx checked, over the front's unix socket, one identity per
  nginx user:

    ```yaml
    runner:
      front:
        listen: unix:/run/flyball/front.sock
        auth: proxy
        proxy:
          preset: custom
          user_header: Remote-User
          grants: {all: [ben]}
    ```

    ```nginx
    location / {
        auth_basic           "lab";
        auth_basic_user_file /etc/nginx/flyball.htpasswd;
        proxy_pass           http://unix:/run/flyball/front.sock:;
        proxy_set_header     Host $http_host;
        proxy_set_header     Remote-User $remote_user;
    }
    ```

- **nginx Basic and the flyball password, both.** Keep `auth: password`
  and clear the header nginx would forward, with
  `proxy_set_header Authorization "";` in the same `location`. flyball
  then sees only its own session cookie, and every action is recorded as
  `local:admin`, not the nginx user.

## The bare runner

`flyball-runner rig.yaml` with no front serves its own door, and the
dashboard too when one is built beside it:

- **Open** (no token, the default): anyone who reaches it may operate the
  rig, so it serves loopback only. Asked for another `--host`, it still
  starts and runs the rig -- a control process that will not start leaves
  the equipment uncontrolled -- but binds `127.0.0.1` on the same port and
  prints one warning line. It answers only the names `localhost`,
  `127.0.0.1` and `[::1]`. `--insecure-open` (or `FLYBALL_INSECURE_OPEN=1`)
  serves it where asked, for that run only, and then it answers, on every
  route, an IP address, `localhost`, or the machine's own name
  (`hostname`, and `<hostname>.local`) -- never another DNS name, which a
  web page elsewhere could point at it (DNS rebinding). Anything else is
  `403`, naming the names it takes.
- **A token** (`runner.auth.token`, `--token`, `--token-file PATH`,
  `FLYBALL_TOKEN`): machines send `Authorization: Bearer T`. For a person,
  the runner prints a one-time link at start:

    ```
    flyball-runner: sign in to the UI once, within 10 minutes: http://127.0.0.1:8000/api/auth/link?n=…
    ```

    Opening it sets a session cookie (in memory, 12 hours) and lands on the
    dashboard with the nonce gone from the address bar. `POST
    /api/auth/link` with the token makes another. The login page also takes
    the token pasted in. A token never goes in a URL: `?token=` is refused.
    Ten different wrong tokens in a minute from one address, pasted or
    sent as a bearer, or a hundred wrong attempts of any kind, and that
    address gets `429` for both until the count has aged below both
    limits -- the right token included, so a script sharing the address
    waits too. A script left polling with an old token is one wrong token,
    however often it asks, and locks no one out. A token shorter than 22
    characters gets a warning at start: make one with
    `python -c 'import secrets; print(secrets.token_urlsafe(16))'`.
    A `--token-file` that cannot be read leaves the runner with a token
    nobody knows, so nothing gets in until it is restarted with a readable
    one.
- `runner.auth.anonymous` (`--anonymous`, `FLYBALL_ANONYMOUS`): `read` lets
  anyone watch -- by an IP address, `localhost` or the machine's own name,
  as for `--insecure-open`. By another DNS name anonymous gets `403` on
  everything that needs a verb, while `/api/auth`, the sign-in and the
  dashboard's own files answer any name, so a person can sign in and a
  session or the token is served by any name.

A bare runner has no passwords. `runner.auth.password`, `.session` and
`.secret`, `--password`, `--session`, `FLYBALL_PASSWORD` and
`FLYBALL_SESSION` are still read, so an old file starts, but ignored with a
warning -- which leaves a runner that had only a password open, so it
serves loopback only. The way up from a bare runner is `flyball run`.

A bare runner with a token that serves beyond loopback warns at start that
the token and cookies cross the network in the clear; it does no TLS.

A bare runner does not belong behind a reverse proxy: it takes the
client's address from the connection, so behind one every client shares
the proxy's address, and one client's wrong tokens lock out all of them.
Behind a proxy, use `flyball run`.

## Stopping the rig

A **software stop** stops the whole rig for everyone at once. Anyone
holding `operate` can do it:

- the **Software stop** button in the dashboard's app bar;
- `flyball stop` (or `flyball stop --all` on every rig a `flyballd` runs);
- `POST /api/rig/stop` with an optional `{"reason": "…"}`;
- the MCP tool `stop_rig`, in `operate` mode;
- `SIGUSR1` to the runner's process, which needs no front, no credential
  and no network: `flyball stop --front-dir DIR`, `flyball stop RIG-FILE`
  (the rig file `flyball run` was started with) and `flyball stop --pid N`
  send it on the rig's host, to the pid in the runner's lock file or the
  one given, and make no HTTP call -- so they cannot stop another rig that
  answers at `$FLYBALL_URL`, and a `503` or `404` there does not block
  them.

All five run the same stop, in this order:

1. **The rig is latched** (the condition `stopped` on the rig), so nothing
   automatic writes from here on ([the latch](#the-latch)).
2. **Every running long command is cancelled**: a dose or a move ends now,
   and its own clean-up still runs.
3. **The program is interrupted**, with a bounded wait for it to unwind.
4. **Every controller goes to manual**, with an `interrupted` event each.
5. **Every device is stopped at once**, each on its own thread, all within
   5 s. A device whose driver has a stop command runs it (the humidity
   blender's `stop`, `mcp4725`'s `power_down`); any other device has each
   writable demand's [resolved stop](../../2-config/devices/index.md#stop-what-a-stop-writes)
   written -- the rig file's `stop:` value, else the driver's `off`, else
   nothing (`keep`: left as it is, energised if it was). These writes go
   past latches, holds, permissives and `max_rate`. A `driver: values`
   device is never stopped: its numbers stay writable.

What was staged -- a value a failed write kept for its retry -- is dropped,
and its retry cancelled. A device that does not finish within the 5 s -- a
stuck rig lock, a bus that does not answer -- does not hold the stop up: it
is reported `failed`, with "may still act" when the time ran out. If a
device's stop command raises, each output's declared `off` is written
instead, and the device is still reported `failed` ("fallback wrote each
declared off", or "fallback: none effective").

It is never rate-limited, and it answers with a report: for each device
its `state` (`stopped`, `unchanged` -- every output kept -- or `failed`),
a `detail`, what it `written` and what it `kept`, by address, with the
value each holds; whether a program was interrupted; which controllers are
in manual; and `latched: true`. A `stop_applied` event lists every output
left energised with its value. A second stop latches nothing new and writes
the stops again.

`GET /api/rig/stop` says, before you need it, what a stop would do to each
output: its stop value (a number, `keep`, or the device's stop command),
where that came from (`off`, `you said`, `nobody said`, `command`), the
controller that drives it, and warnings -- a controller's output nobody
gave a stop, which a stop leaves energised with its controller in manual,
and an unbounded `on_fault: freeze` on an output whose stop is its driver's
`off`. The runner logs the same warnings at start. Every output is
`covered_if_flyball_dies: false`.

A `SIGUSR1` stop writes its report to the runner's log
(`stop report: {…}`) and does not end the process. One that arrives while
the rig is still being built (from the moment the lock file names the
runner; 20 s or so on a Raspberry Pi) finds nothing to stop yet: the log
says so (`SIGUSR1: no rig attached yet`), the runner carries on starting,
and the stop is sent again once it serves. Every stop is in the
[audit](#what-is-recorded), the `SIGUSR1` one as `local:signal`; the
signal's sender is not recorded.

A [rig edit](building.md) runs the same stop before its restart, as a
*planned stop*: the same writes and controllers to manual, but nothing
latched, so the rig comes back passive rather than stopped.

!!! warning "What a stop is not"
    The software stop is a control function, and protection belongs
    outside flyball: wire a hardware stop that removes power, interlocks
    and thermal cut-outs independently of it. It sets only the outputs
    whose inactive level is known, and a crash, `kill -9`, a power loss or
    a hung machine leaves each output at its last value.
    [What flyball does not do](../../0-overview/limits.md) has the full
    list. Before the first unattended run,
    [test a stop, a killed runner and a power cut](index.md#unattended-runs)
    on the real hardware.

### The latch

"Stopped" means flyball refuses its own automatic writes; it does not mean
the outputs are off. While the rig is latched:

- **Refused:** a controller's write (the controller is held, not failed),
  a program step, a trigger, an agent's write or command through MCP, a
  bound input's commit (the device's commit is skipped), and `regulate` --
  so a program's `ramp` or `regulate` step, or an agent, can never clear
  the latch.
- **Allowed:** a person's write or command under `operate`, from the UI or
  the HTTP API (not MCP, not a service token). It goes through, logged as a
  `written_while_stopped` event, and the rig stays latched. A command that
  drives nothing (a setting such as `set_frequency`), a simulation's
  commands and a device's own stop command are never refused; nor are
  writes to a `driver: values` device.

A controller's [`on_fault`](../../2-config/controllers.md#on_fault-what-a-controller-does-about-a-faulty-source)
latches too, with the cause `on_fault:<controller>`: `manual` holds the
controller, `stop` its output as well, `stop_device` the output's whole
device; `stop` and `stop_device` refuse every write to what they hold, a
person's included. Each thing held shows the condition `latched`. The
causes are a set: a thing is free only when no cause holds it, and each
cause is cleared only by its own Reset. `GET /api/rig/latches` lists every
latch held, and `GET /api/health` carries `stopped` and `latches`.

Latches are kept in the store. A runner started again on that store
restores them before it serves and writes the stop they imply again: a
rig stop stops every device, a fault's `stop` or `stop_device` stops what
it held. A runner that crashed with no latch comes back passive: each
driver writes only its build value.

### Reset

```
curl -X POST http://127.0.0.1:8000/api/rig/reset -d '{"cause": "stop"}'
curl -X POST http://127.0.0.1:8000/api/rig/reset -d '{"cause": "on_fault:heaters.heater2"}'
```

`POST /api/rig/reset` lets one cause go (`stop` when the body names none).
It needs `operate` and a person: a service token or an agent through MCP
gets `403`. `404` when nothing holds that cause. It is allowed while the
cause persists, because a Reset resumes nothing: controllers stay in
manual, programs stay ended, outputs stay where the stop left them. A
fault's Reset also clears its controller's law state. The one other way a
latch lets go: a person's `regulate` over HTTP clears the controller's own
`on_fault: manual` latch, which holds nothing else. There is no Reset
button in the UI and no `flyball` subcommand yet.

## What is recorded

- **The front's audit**, one JSON line per sign-in event, in `audit.jsonl`
  beside its tokens file, mode 0600: `login.ok`, `login.fail`, `logout`,
  `token.create`, `token.create.refused`, `token.revoke`, `token.refused`,
  `proxy.refused`, `proxy.peer` (the local user behind a proxy's socket)
  and `fallback`, each with its time, a sequence number and a boot id
  (`token.revoke` also with its `outcome`). A line left torn by a crash is
  ended before the next record, so only that line is lost. A sign-in or a new token whose
  record cannot be written does not happen (`503`); a revoke still happens,
  and its `503` says so. If the file cannot be opened at all, the front
  still serves the rig and says so at start (with the reason) and in the
  dashboard's banner (without it: anyone who reaches the front reads the
  banner, and the reason names a path), refuses every sign-in and new token, and tries the file again (at most
  every 5 seconds) until it opens -- fixing it needs no restart. `flyball token create` and `flyball token revoke`, which work
  on the files directly, append their `token.create` and `token.revoke` to
  the same file, by `local:cli`, under the same rule: no record, no new
  token; a revoke still happens and exits non-zero saying so.
- **The runner's audit**, the `audit` table in the rig's store: one row for
  every request that needs more than `read` from a caller who is not
  anonymous -- refused ones included -- and every stop, `SIGUSR1` included:
  who (`sub`, session, kind, from where), what, and how it ended. It is
  append-only and outside retention, so what an unknown caller asks is not
  kept there, and one caller's refusals are kept to ten rows a minute (a
  refused demand to its first 16 signals); the runner's log counts the
  rest. [Storage](../../6-internals/db.md)
  has the columns. A write that fails is logged and refuses nothing: a
  full disk does not block a stop.

## A sub-path

Under `flyballd` each rig is served under its manifest's `root_path`
(`/furnace/`), by the daemon's front. A bare runner takes `--root-path
/flyball/humidity` (or `FLYBALL_ROOT_PATH`) and serves everything under
that prefix: `/flyball/humidity/api`, `/flyball/humidity/ws`,
`/flyball/humidity/mcp`, `/flyball/humidity/docs`; the root is 404. For
several bare runners on one domain behind a proxy that passes the path
through unchanged -- one runner and one `location` each, no rewriting:

```nginx
location /flyball/humidity/api/ { proxy_pass http://127.0.0.1:8001;
                                  proxy_set_header Host $http_host; }
location /flyball/humidity/ws/  { proxy_pass http://127.0.0.1:8001;
                                  proxy_set_header Host $http_host;
                                  proxy_http_version 1.1;
                                  proxy_set_header Upgrade $http_upgrade;
                                  proxy_set_header Connection "upgrade"; }
location /flyball/humidity/     { alias /srv/flyball/dist/;
                                  try_files $uri /index.html; }
```

The UI build is the same for every path: its assets are relative, and it
finds its API from where the page was served (`https://host/flyball/humidity/`
→ `https://host/flyball/humidity/api`). The Python client takes the prefix
in the URL (`Rig("https://host/flyball/humidity")`); the CLI takes it via
`FLYBALL_URL=https://host/flyball/humidity`.

`--no-mcp` (or `FLYBALL_NO_MCP=1`) leaves the MCP servers off: the runner
serves `/api` and `/ws` only, and `/mcp/…` is 404. For a rig a model has no
business driving.

## Stopping and restarting the runner from the API

`POST /api/runner/shutdown` ends the runner as Ctrl-C would; `POST
/api/runner/restart` does that and then starts the same command line again
in the same process id, so a supervisor sees nothing. Both need `operate`,
and both are 409 unless the runner runs with `--allow-shutdown`
(`runner.allow_shutdown`). A change to the rig made over the API saved
itself (the overlay beside the rig file, or the store for a runner with no
file), so it comes back; a controller attached since the start and not
saved does not. A change to the rig restarts the runner too, the same way,
and needs no `--allow-shutdown`
([Building a rig while it runs](building.md)). This ends the process; the
[software stop](#stopping-the-rig) above leaves it running.
