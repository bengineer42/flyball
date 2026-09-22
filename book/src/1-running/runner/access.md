# Access: the door, a sub-path, stopping

Who may reach the runner, where, and what the API is allowed to do to the process. All of it is set by flags or the [`runner:` section](../../2-config/runner.md) of the config file.

## The door: a password, a token, or open

Three ways a runner can stand:

- **Open** (the default): no password, no token; anyone who can reach the
  port can read and drive the rig. Fine on loopback, so it is served on
  loopback only: an open runner with `--host` anything else (`0.0.0.0`, a
  LAN address, a host name other than `localhost`) refuses to start, and
  says how to fix it. To serve one open on the network anyway, say so:
  `--insecure-open` (`auth.insecure_open: true`), and the runner logs a
  warning each start. Not on a rig a model can drive.
- **A password** (`--password P`, `FLYBALL_PASSWORD`, or `auth.password` in
  the [`runner:` section](../../2-config/runner.md)): for a person at the UI.
  The login page trades it for a session -- an `HttpOnly` cookie the browser
  then carries on every request, socket and download by itself -- so the
  browser never keeps the password. The value may be the plain text, or the
  hashed line `flyball password` prints (`$scrypt$…`), which is what belongs
  in a file that is committed anywhere.
- **A token** (`--token T`, `FLYBALL_TOKEN`, `auth.token`): for machines. The
  Python client (`flyball.interfaces.client.Rig`), `flyball-mcp` and any script send it
  as `Authorization: Bearer T`; a websocket, or a plain `GET` the browser
  navigates to (an export link), may pass `?token=T` instead, since a browser
  cannot set headers on either -- a URL is logged where a header is not, so
  the header is the form to use wherever it can be set. The Go CLI
  (`flyball`) takes it the same way -- `flyball --token T ...` or
  `FLYBALL_TOKEN=T` in the environment. The login page takes the token too,
  so a browser on a token-only runner still ends up with a cookie and
  nothing in its storage.

A password or a token given in the environment counts the same as one on
the command line or in the file.

**Plain HTTP.** The runner does not do TLS: a password, a token and a
session cookie sent to it from another machine cross the network in the
clear, and anyone on the path can take them. A runner with a password or a
token that serves beyond loopback logs a warning saying so at start; put a
proxy that terminates TLS in front of it on anything but a network you
trust.

Either one shuts the door: everything under `/api`, `/ws` and `/mcp` needs a
session or the token, bar `/api/auth` (the door itself) and `/docs`.
Refused is `401` with a `detail` (a socket is closed with code 4401).

**Who may look without either** is `auth.anonymous` (`--anonymous`,
`FLYBALL_ANONYMOUS`): `none` (the default -- nothing until signed in) or
`read` -- every `GET` and every stream is served to anyone, and only a
session or the token may do anything else. `read` is how a rig goes on the
public internet to be watched but not driven, with or without a proxy's
`limit_except GET` in front of it; the one `GET` with a side effect,
`/api/probe`, stays behind the door. With `read` the UI shows the rig
read-only, says so in the app bar, and offers to sign in when a control is
refused.

A session lasts `auth.session` (`--session`; default `12h`). Sessions are
signed, not stored: the key is `auth.secret` if given, else a file
`<store>.key` beside the store (made on first use, readable by the owner
only), else one made for the process -- in which case a restart signs
everyone out. Changing the password signs everyone out too. Ten wrong
passwords in a minute from one address are refused for the rest of it.

The runner's own MCP mount still works on a password-only runner (it uses a
token of its own, never shown); a model connecting from outside needs the
runner to have `--token` as well. `GET /api/runner` reports none of these
values; `GET /api/auth` says which the runner has.

## Behind `flyball run --serve-ui`

`flyball run rig.yaml --serve-ui ADDR` (or `runner.run.serve_ui`) serves the
UI on `ADDR` and proxies `/api`, `/ws` and `/mcp` to the runner on loopback,
with no door of its own -- so the runner's door is the only one, and the
runner, being on loopback, would not refuse to be open. The front makes the
same decision for `ADDR` instead: on an address beyond loopback (`:8000` is
every interface) it serves nothing until the runner answers
`GET /api/auth`, and

- a runner with no password and no token -- in the file, on the command
  line or in its environment -- is stopped, and `flyball run` exits 1 with
  a message naming the fixes;
- unless `--insecure-open` is given (it is passed on to the runner too) or
  the file says `runner.auth.insecure_open: true`: then it is served, with
  a warning;
- a runner with either is served, with the plain-HTTP warning.

A runner that answers `/api/auth` with anything but its door (a 404, a
page that is not JSON) counts as open. `flyballd` does the same for every
runner it proxies to when its `listen` is beyond loopback: an open runner's
routes answer 503 unless `auth.insecure_open` is set in `flyballd.yaml`
([the daemon](../../7-reference/cli.md#the-daemon)).

For the usual Pi setup, that means a password in the file:

```yaml
runner:
  auth:
    password: $scrypt$…      # flyball password
  run:
    serve_ui: ":8000"
```

## The password, from the Go CLI

The Go CLI signs in the same way as the UI's login page: `flyball login`
prompts for the password (or takes it as an argument, `flyball login
SECRET` -- careful, that lands in shell history), POSTs it to
`/api/auth/login`, and saves the session cookie the runner returns to a
file under `$XDG_CONFIG_HOME/flyball` (`~/.config/flyball` on Linux), one
file per runner URL, mode `0600`. Every later `flyball` invocation against
that same URL picks the saved cookie back up automatically -- no need to
log in again until the session expires (`--session`, default 12h) or
`flyball logout` clears it. A wrong password is refused (401, after a
short pause; ten wrong ones in a minute from one address are 429). This is
separate from the daemon's own access control (`flyballd`'s `auth.token`,
[the CLI reference](../../7-reference/cli.md#the-daemon))
-- `flyball login` authenticates to a *runner*, whether reached direct
(`FLYBALL_URL`) or through the daemon's proxy (`-s`/`FLYBALLD_URL`).

`--no-mcp` (or `FLYBALL_NO_MCP=1`) leaves the MCP servers off: the runner
serves `/api` and `/ws` only, and `/mcp/…` is 404. For a rig a model has no
business driving, or one that a proxy exposes read-only.

## A sub-path

`--root-path /flyball/humidity` (or `FLYBALL_ROOT_PATH`) serves everything
under that prefix: `/flyball/humidity/api`, `/flyball/humidity/ws`,
`/flyball/humidity/mcp`, `/flyball/humidity/docs`; the root is 404. For
several rigs on one domain behind a proxy that passes the path through
unchanged -- one runner and one `location` each, no rewriting:

```nginx
location /flyball/humidity/api/ { proxy_pass http://127.0.0.1:8001; }
location /flyball/humidity/ws/  { proxy_pass http://127.0.0.1:8001;
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

## Stopping and restarting from the API

`POST /api/runner/shutdown` stops the runner as Ctrl-C would; `POST
/api/runner/restart` does that and then starts the same command line again
in the same process id, so a supervisor sees nothing. Both are 409 unless
the runner runs with `--allow-shutdown` (`runner.allow_shutdown`). What was
built over the API and not saved is gone across a restart unless the
runner runs with `--resume`.
