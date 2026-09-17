# Access: the token, a sub-path, stopping

Who may reach the daemon, where, and what the API is allowed to do to the process. All of it is set by flags or the [`daemon:` section](../../2-config/daemon.md) of the config file.

## The token

`--token T` (or `FLYBALL_TOKEN=T`) makes every request to `/api`, `/ws` and
`/mcp` require `Authorization: Bearer T`; a websocket, or a plain `GET`
the browser navigates to (an export link), may pass `?token=T` instead,
since a browser cannot set headers on either -- a URL is logged where a
header is not, so the header is the form to use wherever it can be set.
Anything else is 401
with a `detail` (a socket is closed with code 4401). The CLI, the client
and `flyball-mcp` take `--token` or the same variable; the UI asks for it.
Without a token the daemon serves anyone who can reach the port -- fine on
loopback, not on `--host 0.0.0.0`, and not on a rig a model can drive.

`--no-mcp` (or `FLYBALL_NO_MCP=1`) leaves the MCP servers off: the daemon
serves `/api` and `/ws` only, and `/mcp/…` is 404. For a rig a model has no
business driving, or one that a proxy exposes read-only.

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
