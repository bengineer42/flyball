# Your own code

**What.** Everything the UI does goes through the HTTP and websocket API, so
a script, a notebook, a LabVIEW box or another service can do the same.

| client | language | |
| --- | --- | --- |
| `flyball.client.Rig` | Python | a method per device command synthesised from the schema, `read`, `demand`, `watch`, and `get`/`post`/`put` for any route; the runner and MCP server build on it |
| `@flyball/client` (`ui/packages/client`) | TypeScript | the same, typed from the wire format; the UI is built on it |
| `curl`, `requests`, a browser | anything | plain JSON over HTTP; downloads are `GET`s with `?token=` |

**Configure.** A URL (with the runner's `--root-path` prefix if it has
one) and, for a runner with a token, `Authorization: Bearer`.

**Everything else** is [The server](../4-server/index.md): the routes, the
wire format, errors, authentication.
