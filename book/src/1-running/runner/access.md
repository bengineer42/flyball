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
page on the same site as that `Host`.

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
asked to listen on answers every request `503` with the reason, and the
`local` shape is served somewhere else: a fresh port on `127.0.0.1`, or a
socket beside a `unix:` one (`front.sock.local` next to `front.sock`). A
reverse proxy on the same machine keeps forwarding to the address it was
given, and must not find an open console there:

```
flyball: front: the password is not a $scrypt$ line (…); plaintext passwords are refused -- `flyball password` makes one -- 127.0.0.1:8000 answers 503 (auth misconfigured), and the local shape is served on 127.0.0.1:0 only (a fresh port: the line saying where it serves names it); the rig keeps running (D-028)
flyball: serving rig furnace on http://127.0.0.1:40321/ (local)
```

A `listen` that does not parse serves the `local` shape on
`127.0.0.1:8000`, with nothing to refuse.

The reason is in `GET /api/auth` (`exposure.warning`, with `exposure.port`
the console's), and a `fallback` record goes to the front's
[audit](#what-is-recorded). The `local` shape asked for an address beyond
loopback falls back to `127.0.0.1` on the same port. To serve it on the network anyway, say so for that run:
`--insecure-open`, or `FLYBALL_INSECURE_OPEN=1` in the environment of
`flyball run` or `flyballd`. There is no file key for it: a file can be
copied from anywhere, and `extends:` would pass it on. The front then
answers any name, warns at every start, and the dashboard shows a banner
that cannot be dismissed. Not on a rig a model can drive.

## Signing in, sessions and tokens

**People**, at a `password` front, sign in at the dashboard's login page
with the admin password. The front keeps the session in memory and gives
the browser an `HttpOnly` cookie (`flyball-<port>`, or `__Host-flyball`
under HTTPS); the page keeps no secret. A session ends after 12 idle hours
(`session:` changes that), 7 days at most, at **Sign out**, or when the
front restarts. A wrong password is refused after half a second; ten wrong
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
another machine. `flyball token list` and `flyball token revoke ID` work on
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
These two verbs are a placeholder: which verbs there are, and what each
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
bit, or in one of its own that others may enter. A web server proxy
usually runs as another user (`www-data`, `caddy`), so give the directory to
the front's user and the proxy's group, setgid so the socket inherits that
group, and put nobody else in the group (`tailscaled` runs as root and
needs no group):

```sh
sudo install -d -o flyball -g www-data -m 2750 /run/flyball
```

A `systemd` unit can do the same with `RuntimeDirectory=flyball`,
`RuntimeDirectoryMode=2750` and `Group=www-data`. Every member of that group
can assert any identity; the audit records the local user behind each new
one (`proxy.peer`), after the fact.

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

## The bare runner

`flyball-runner rig.yaml` with no front serves its own door, and the
dashboard too when one is built beside it:

- **Open** (no token, the default): anyone who reaches it may operate the
  rig, so it serves loopback only. Asked for another `--host`, it still
  starts and runs the rig -- a control process that will not start leaves
  the equipment uncontrolled -- but binds `127.0.0.1` on the same port and
  prints one warning line. It answers only the names `localhost`,
  `127.0.0.1` and `[::1]`. `--insecure-open` (or `FLYBALL_INSECURE_OPEN=1`)
  serves it where asked, for that run only.
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
    Ten wrong tokens in a minute from one address, pasted or sent as a
    bearer, and that address gets `429` for both until the oldest is a
    minute old -- the right token included, so a script sharing the
    address waits too.
    A `--token-file` that cannot be read leaves the runner with a token
    nobody knows, so nothing gets in until it is restarted with a readable
    one.
- `runner.auth.anonymous` (`--anonymous`, `FLYBALL_ANONYMOUS`): `read` lets
  anyone watch.

A bare runner has no passwords. `runner.auth.password`, `.session` and
`.secret`, `--password`, `--session`, `FLYBALL_PASSWORD` and
`FLYBALL_SESSION` are still read, so an old file starts, but ignored with a
warning -- which leaves a runner that had only a password open, so it
serves loopback only. The way up from a bare runner is `flyball run`.

A bare runner with a token that serves beyond loopback warns at start that
the token and cookies cross the network in the clear; it does no TLS.

## Stopping the rig

A **software stop** interrupts any program and puts every controller in
manual, for everyone at once. Anyone holding `operate` can do it:

- the **Software stop** button in the dashboard's app bar;
- `flyball stop` (or `flyball stop --all` on every rig a `flyballd` runs);
- `POST /api/rig/stop` with an optional `{"reason": "…"}`;
- the MCP tool `stop_rig`, in `operate` mode;
- `SIGUSR1` to the runner's process, which needs no front, no credential
  and no network: when the front cannot be reached or does not answer
  within 5 seconds, `flyball stop` sends
  it to the pid in the runner's lock file (`--front-dir DIR`, `--pid N`, or
  the rig file `flyball run` was started with).

It is never rate-limited, and it answers with a report: what happened to
each device, whether a program was interrupted, which controllers are in
manual. **In this release it writes nothing to any device**: outputs are
left at whatever they were last told, and each device is reported
`unchanged` (`interim: true` in the report). A `SIGUSR1` stop writes its
report to the runner's log (`stop report: {…}`) and does not end the
process. Every stop is in the [audit](#what-is-recorded), the `SIGUSR1` one
as `local:signal`; the signal's sender is not recorded.

!!! warning "Not an emergency stop"
    The software stop is a control function, not an emergency stop in the
    sense of IEC 60204-1 or ISO 13850, and flyball is not a safety system.
    Put the protection outside it: thermal cut-outs, a hardware emergency
    stop that removes power, and wiring such that de-energised is safe. A
    crash, `kill -9`, a power loss or a hung machine leaves each output at
    its last value, and so does this stop. Before the first unattended run,
    [test a stop, a killed runner and a power cut](index.md#unattended-runs)
    on the real hardware.

## What is recorded

- **The front's audit**, one JSON line per sign-in event, in `audit.jsonl`
  beside its tokens file, mode 0600: `login.ok`, `login.fail`, `logout`,
  `token.create`, `token.create.refused`, `token.revoke`, `token.refused`,
  `proxy.refused`, `proxy.peer` (the local user behind a proxy's socket)
  and `fallback`, each with its time, a sequence number and a boot id
  (`token.revoke` also with its `outcome`). A sign-in or a new token whose
  record cannot be written does not happen (`503`); a revoke still happens,
  and its `503` says so. If the file cannot be opened at all, the front
  still serves the rig and says so at start, but refuses every sign-in and
  new token. `flyball token create` and `flyball token revoke`, which work
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
(`runner.allow_shutdown`). What was built over the API and not saved is gone
across a restart unless the runner runs with `--resume`. This ends the
process; the [software stop](#stopping-the-rig) above leaves it running.
