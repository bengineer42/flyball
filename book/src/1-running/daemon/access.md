# Access: the door, a sub-path, stopping

Who may reach the daemon, where, and what the API is allowed to do to the process. All of it is set by flags or the [`daemon:` section](../../2-config/daemon.md) of the config file.

## The door: a password, a token, or open

Three ways a daemon can stand:

- **Open** (the default): no password, no token; anyone who can reach the
  port can read and drive the rig. Fine on loopback; not on `--host 0.0.0.0`,
  and not on a rig a model can drive.
- **A password** (`--password P`, `FLYBALL_PASSWORD`, or `auth.password` in
  the [`daemon:` section](../../2-config/daemon.md)): for a person at the UI.
  The login page trades it for a session -- an `HttpOnly` cookie the browser
  then carries on every request, socket and download by itself -- so the
  browser never keeps the password. The value may be the plain text, or the
  hashed line `flyball password` prints (`$scrypt$…`), which is what belongs
  in a file that is committed anywhere.
- **A token** (`--token T`, `FLYBALL_TOKEN`, `auth.token`): for machines. The
  CLI, `flyball-mcp`, `flyball.client.Rig` and any script send it as
  `Authorization: Bearer T`; a websocket, or a plain `GET` the browser
  navigates to (an export link), may pass `?token=T` instead, since a browser
  cannot set headers on either -- a URL is logged where a header is not, so
  the header is the form to use wherever it can be set. The login page takes
  the token too, so a browser on a token-only daemon still ends up with a
  cookie and nothing in its storage.

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

The daemon's own MCP mount still works on a password-only daemon (it uses a
token of its own, never shown); a model connecting from outside needs the
daemon to have `--token` as well. `GET /api/daemon` reports none of these
values; `GET /api/auth` says which the daemon has.

## A sub-path

`--root-path /flyball/humidity` (or `FLYBALL_ROOT_PATH`) serves everything
under that prefix: `/flyball/humidity/api`, `/flyball/humidity/ws`,
`/flyball/humidity/mcp`, `/flyball/humidity/docs`; the root is 404. For
several rigs on one domain behind a proxy that passes the path through
unchanged -- one daemon and one `location` each, no rewriting:

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
→ `https://host/flyball/humidity/api`). The client and CLI take the prefix
in the URL: `flyball --url https://host/flyball/humidity`.

## Stopping and restarting from the API

`POST /api/daemon/shutdown` stops the daemon as Ctrl-C would; `POST
/api/daemon/restart` does that and then starts the same command line again
in the same process id, so a supervisor sees nothing. Both are 409 unless
the daemon runs with `--allow-shutdown` (`daemon.allow_shutdown`). What was
built over the API and not saved is gone across a restart unless the
daemon runs with `--resume`.
