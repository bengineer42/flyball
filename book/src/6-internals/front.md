# The front and the runner

The front (`daemon/internal/front`, Go) is the one component that decides
who a caller is; the runner (Python) decides what each route needs. One
package serves both `flyball run` and `flyballd`. What a site configures
is in [Access](../1-running/runner/access.md); this page is how the two
halves talk.

```
browser / CLI / MCP ── TLS or loopback ──▶ front                 ── unix socket ──▶ runner
proxy (Authelia, …) ────────────────────▶ · who: token, session,    in a 0700       · verifies the principal
                                            proxy, anonymous         front-dir       · the verb table: route → verb
                                          · Host, Origin, TLS                        · the action audit
                                          · mints the principal
                                          · serves the dashboard
```

## The front-dir

The front makes one directory per runner, mode 0700 and owned by its own
user, checked with `lstat` (not a symlink) before every spawn:

- under systemd with `RuntimeDirectory=flyball`, `/run/flyball/<name>/`;
- otherwise `$XDG_RUNTIME_DIR/flyball/<front-id>/<name>/`, the front-id
  being the first 8 hex digits of the SHA-256 of the absolute path of the
  front's config (the rig file for `flyball run`, `flyballd.yaml` for the
  daemon); `flyball run` names its one runner `run`;
- with neither, or when the socket path would pass 100 bytes (`sun_path`),
  a fresh temporary directory -- which a restarted front cannot find, so
  there is no [adoption](#adoption) from one.

| file | written by | mode | holds |
| --- | --- | --- | --- |
| `key` | the front, fresh at **every** spawn | 0600 | 64 lower-case hex characters (32 bytes) and a newline |
| `aud` | the front | 0600 | the audience: the manifest name under `flyballd`, `run-<8 hex>` under `flyball run` |
| `endpoint` | the front | 0600 | `unix:<front-dir>/sock`, or `tcp:127.0.0.1:<port>` on Windows only |
| `sock` | the runner | 0600, bound so before it listens | |
| `runner.lock` | the runner, `flock`ed for its life; the front creates it and holds it while it writes the directory, emptying it (the pid it named has exited) | 0600 | `pid <n> rig <name>` (`pid <n>` until the rig file is read); empty from a front's write until the runner, just after it takes the lock, names itself |

The front passes the directory in argv, `flyball-runner --front-dir DIR`;
the key is never in argv or the environment. A runner given a front-dir
that is unsafe, or whose `key`, `aud` or `endpoint` is missing or
malformed, exits **4** before it takes the rig's lock or touches hardware;
the front rewrites the directory and starts it once more. A second runner
for the same store exits **3**: the rig's own `<store>.lock` is held. The
runner takes `runner.lock` first, before it reads `key` (exit **3** if
another runner holds it; a front's probe or write, which holds it for an
instant, is waited out), and the front never rewrites `key` while
`runner.lock` is held -- so a front that starts while a runner is starting
finds it, rather than giving a second runner a new key. A build for a
platform with no `flock` (Windows) cannot tell a held `runner.lock`: there
a restarted front rewrites a live runner's key, and the rig is protected
only by its `<store>.lock`.

TCP is used only where a unix socket cannot be: on Windows, where it is
the default. The same signed principal protects both, but over TCP the
runner never proves it holds the key: a loopback port is open to every
local user, and one who binds it first (while the runner is down, or
before a slow one binds) passes the readiness probes and is taken for the
runner. So TCP is refused everywhere but Windows until the runner proves
its key (D-044). On Linux and macOS a manifest that says `network: tcp`
still starts its rig, on the unix socket in its front-dir, and `flyballd`
logs a warning that says so -- a misconfiguration removes exposure, never
operation (D-028); a front-dir whose `endpoint` is `tcp:` is refused by
both sides (the runner exits **4**). Anything that can share the runner's
network namespace and its front-dir can use the socket in it, so nothing
is lost there. flyball does not run on Windows yet (a port is planned);
on Windows `flyballd` logs a warning when it starts a runner on TCP.

## Readiness

A runner counts as running only after two probes over its endpoint: an
unsigned `GET <root>/api/auth/front` must get `401`, which shows it enforces
the principal, and a signed one must get `200`
`{"protocol": 1, "aud", "pid", "flyball"}`. A socket that is not there yet
counts as starting (the front answers `503` with `Retry-After: 1`, and the
dashboard shows *starting…*). A runner that answers otherwise -- one too old
to know `--front-dir` exits 2 on the unknown flag -- is never proxied to:
the front answers `502` "too old for this front".

## The principal

One header, `X-Flyball-Principal`, minted by the front for each request,
HMAC-SHA256 with the runner's `key`:

```
token   = "v1." B64(payload) "." B64(HMAC-SHA256(key, "v1." B64(payload)))
B64     = base64url, no padding
payload = JSON, keys in this order, no whitespace:
          sub, nm?, sid, scp, kind, aud, cip, sch, via?, iat, exp     (? omitted when empty)
```

| claim | |
| --- | --- |
| `sub` | who: `local:console` (the `local` shape), `local:admin` (the password), `token:<name>`, `proxy:<issuer>#<subject>`, `anon:`, `front:probe` |
| `nm` | a display name, for the audit and the UI; never authorises |
| `sid` | a random id per session, token or anonymous request; nothing derived from a cookie |
| `scp` | the caller's verbs **on this rig**, expanded, sorted, unique |
| `kind` | `human`, `service` or `agent` |
| `aud` | the runner's `aud`, so a principal for one rig is refused by another |
| `cip`, `sch` | the client's address as the front saw it (`""` if unknown), and `http` or `https`: `https` under the front's own TLS, or with an `https` `url` when the peer is this machine or in `trusted_proxies` -- the hop, not what `url` claims |
| `via` | `mcp` when the runner re-minted it for an MCP tool's call |
| `iat`, `exp` | Unix seconds; 60 s apart |

The runner checks, in order, each failure with a fixed code sent back as
`X-Flyball-Principal-Error`: `format` (three parts, at most 4096 bytes,
exactly one such header and no other `x-flyball-*` header in any
spelling), `version`, `mac` (over the bytes received, constant-time),
`json`, `claims`, `aud`, `lifetime` (`0 < exp − iat ≤ 120`), `expired`
(`now > exp + 5`), `future` (`iat > now + 5`). Unknown claims are ignored,
so a front and a runner of different releases can meet. Go and Python
share one set of golden vectors,
`daemon/internal/principal/testdata/principal-v1.json`.

A refused principal is the front's and the runner's disagreement, not the
caller's fault: the front logs the code and answers `502` (a websocket is
closed with 1014), and does not sign anyone out.

## What the front passes on

The front builds the runner's request headers from an allow-list: content
negotiation and caching headers, `Range`, `User-Agent`, `Last-Event-Id`,
the MCP session headers, trace context, and the websocket handshake. A
header is copied only in its canonical spelling; an underscore or other
variant is dropped. `Authorization`, `Cookie`, `Origin`, `Referer`, every
`X-Forwarded-*` and `Forwarded`, and every `x-flyball-*` never reach a
runner. The front sets `Host: localhost`, the principal and `X-Request-Id`.
From the runner's answer it drops `Set-Cookie` and adds
`X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`.
Only `<root>/api`, `<root>/ws` and `<root>/mcp` are proxied; the front
answers `<root>/api/auth*` itself and serves the dashboard for everything
else, so a fronted runner's `/docs` is not reachable through it.

A `GET` or a stream is cut when the credential behind it ends (sign-out,
revocation, expiry), within a second; a write already on its way to
hardware is not.

A caller who holds no verb on the rig at all (anonymous under `anonymous:
none`, a token for other rigs) is refused by the front without a
connection to the runner, with the runner's own answer: `401` (a socket
closed with 4401) with no credential, `403` `{detail, needed}` (4403)
otherwise, `needed` being `read` for a `GET` or a socket and `operate` for
anything else. Every route behind `/api`, `/ws` and `/mcp` needs a verb, so
nothing is lost.

Each websocket and each event stream (a `GET` asking for
`text/event-stream`: MCP's server stream) holds a connection to the runner
for as long as it lasts, and a runner inherits a soft limit of 1024
descriptors, beyond which it can accept nothing -- the dashboard's stop
included -- and open no device. So the front holds at most 512 of them per
rig, 128 of those for callers with no credential (anonymous viewers, or the
`local` shape's console), so that anonymous viewers cannot shut a signed-in
operator out. Over the cap a socket is closed with 1013 (try again later;
the dashboard retries) and a stream is `429` with `Retry-After`. A `POST` is
never counted, so the stop always gets through. Raising the runner's limit
instead would move the failure onto serial links: pyserial's `select()`
refuses a descriptor above 1024.

## The verb table

`flyball.interfaces.server.verbs` has one row per route and method: the
verb the request needs. A route with no row is refused for everyone
(`403`, `needed: null`), and a test walks the app's routes and fails on any
without one. The front makes no decision by path: it puts the caller's
verbs in the principal, and the runner compares.

The vocabulary is a placeholder until D-034 is decided: two verbs, `read`
(every `GET` and stream, `POST /api/rig/check`, `POST
/api/programs/check`) and `operate` (everything else, `POST /api/rig/stop`
included). `/mcp/read` needs `read`; `/mcp/author` and `/mcp/operate` need
`operate`. The same list is `daemon/internal/grants/vocabulary.json` on
the Go side, with the roles `all` and `viewer` and the management scope
`manage`; a test keeps the two equal. D-034 changes data here, not code.

A scope is `<verb>:<rig>`, `<verb>:*`, a bare verb (every rig), or
`manage`. The front expands a caller's scopes for the rig a path routes
to; a token's are also cut to what its issuer holds now.

## MCP's own calls

An MCP tool calls the runner's own HTTP API. Each call carries a principal
the runner mints for that one request, with its own key -- the front-dir's,
or an in-memory one on a bare runner: the caller's `sub`, `sid`, `kind`,
`cip` and `sch`, `scp` = the caller's verbs ∩ the mode's, and `via: mcp`.
Listing tools and refreshing the schema use a `read` principal of the
runner's own (`runner:mcp`). No standing credential exists for it.
`check_driver` and `search_drivers`, which run a file named by the caller
on the host, are on the stdio server (`flyball-mcp`) only.

## Adoption

`flyballd` does not stop its runners when it stops, however it stops
(D-037). A starting `flyballd` finds each rig's front-dir; if a runner
holds its `runner.lock`, it reads `key`, `aud` and `endpoint`, checks the
runner with the signed handshake, and routes to it again -- same process,
same key, no interruption. `GET /api/runners` then says `adopted: true`
and the `pid`. A runner that holds the lock but has not yet named itself
in it, is not listening yet, or does not answer the probe in time (a
timeout, or a connection closed unanswered: a Raspberry Pi takes ~20 s to
start) is starting, and is tried again; one still not answering after
60 s, or one whose answer shows it is not this front's (another audience,
an old runner), is left alone and the rig is `busy`, with the `reason`.
An adopted runner is not `flyballd`'s child, so its exit status cannot be
known: when its pid goes, the manifest's `restart` policy treats it as a
crash. A runner spawned into a front-dir that another runner took
meanwhile exits 3 on its `runner.lock`; that one is adopted the same way.
A runner in a temporary front-dir (no private runtime directory, or one
too deep for a socket path under it) cannot be found: `flyballd` warns of
it at start. `flyball run` has no adoption: when it ends normally its
runner ends first -- a second Ctrl-C hurries the runner (SIGINT again) and
a third kills its group, and `flyball run` still waits for it (D-045) --
but a `flyball run` that is killed (`SIGKILL`, a crash) leaves its runner
running, to be ended with `kill <pid>` (`runner.lock` names it).
