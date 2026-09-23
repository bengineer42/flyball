# Access: the door, a sub-path, stopping

Who may reach the runner, where, and what the API is allowed to do to the process. All of it is set by flags or the [`runner:` section](../../2-config/runner.md) of the config file.

## The door: a password, a token, or open

Three ways a runner can stand:

- **Open** (the default): no password, no token; anyone who can reach the
  port can read and drive the rig. For one person on their own machine: an
  open runner answers only when it is addressed as `localhost`,
  `127.0.0.1` or `[::1]` (any port), and refuses every other name with
  `403` -- so it cannot be reached across the network even on `--host
  0.0.0.0`, and a web page that points its own name at your machine (DNS
  rebinding) is refused too. To reach a runner by any other name, give it
  a password or a token.
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
  cannot set headers on either -- a URL is logged where a header is not
  (the runner's own request log drops the query, but a proxy's may not), so
  the header is the form to use wherever it can be set. The Go CLI
  (`flyball`) takes it the same way -- `flyball --token T ...` or
  `FLYBALL_TOKEN=T` in the environment. The login page takes the token too,
  so a browser on a token-only runner still ends up with a cookie and
  nothing in its storage.

Either one shuts the door: everything under `/api`, `/ws` and `/mcp` needs a
session or the token, bar `/api/auth` (the door itself). The rest -- the
bundled UI, whose login page has to load before anyone has signed in,
`/docs` and `/openapi.json` -- is open to a `GET`. Refused is `401` with a
`detail` (a socket is closed with code 4401). A token that is sent and
wrong is refused the same way, even where anonymous callers may read.

**Other web pages.** Whatever the door, a request that changes something
(anything but `GET`, `HEAD` and `OPTIONS`) and every websocket is refused
with `403` when the browser says it comes from another site: an `Origin`
header that is not the runner's own address, or `Origin: null`. A page on
another site therefore cannot drive the rig through your browser -- not
with your session cookie, and not on an open runner. Tools that send no
`Origin` (the CLI, the Python client, `curl`) are unaffected, and so is
anything that sends the bearer token, which another site cannot know.
Behind a proxy, pass the browser's `Host` through (nginx: `proxy_set_header
Host $http_host;`), or the runner cannot tell its own address from
another's; a TLS proxy (`https://` outside, plain HTTP to the runner) is
recognised as the same site.

**Who may look without either** is `auth.anonymous` (`--anonymous`,
`FLYBALL_ANONYMOUS`): `none` (the default -- nothing until signed in) or
`read` -- every `GET` and every stream is served to anyone, and only a
session or the token may do anything else. `read` is how a rig goes on the
public internet to be watched but not driven, with or without a proxy's
`limit_except GET` in front of it -- no `GET` has a side effect (a bus
probe is `POST /api/probe`). With `read` the UI shows the rig
read-only, says so in the app bar, and offers to sign in when a control is
refused.

A session lasts `auth.session` (`--session`; default `12h`). Sessions are
signed, not stored: the key is `auth.secret` if given, else a file
`<store>.key` beside the store (made on first use, readable by the owner
only), else one made for the process -- in which case a restart signs
everyone out. Changing the password signs everyone out too. Ten wrong
passwords in a minute from one address are refused for the rest of it.
The address is the connection's own: the runner trusts no
`X-Forwarded-For` (so a caller cannot pick a fresh one per guess), which
means that behind a proxy every caller shares the proxy's address and
its ten. Checking a hashed password takes a moment and some memory, so
the runner checks two at a time, off the loop that serves everything
else, and answers a third `429` straight away. The cookie is marked `Secure` over `https`, or when a TLS proxy
says `X-Forwarded-Proto: https`.

The runner's own MCP mount still works on a password-only runner (it uses a
token of its own, never shown); a model connecting from outside needs the
runner to have `--token` as well. `GET /api/runner` reports none of these
values; `GET /api/auth` says which the runner has.

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

## Stopping and restarting from the API

`POST /api/runner/shutdown` stops the runner as Ctrl-C would; `POST
/api/runner/restart` does that and then starts the same command line again
in the same process id, so a supervisor sees nothing. Both are 409 unless
the runner runs with `--allow-shutdown` (`runner.allow_shutdown`). What was
built over the API and not saved is gone across a restart unless the
runner runs with `--resume`.
