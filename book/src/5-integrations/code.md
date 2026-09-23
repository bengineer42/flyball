# Your own code

**What.** Everything the UI does goes through the HTTP and websocket API, so
a script, a notebook, a LabVIEW box or another service can do the same.

| client | language | |
| --- | --- | --- |
| `flyball.interfaces.client.Rig` | Python | a method per device command synthesised from the schema, `read`, `demand`, `watch`, and `get`/`post`/`put` for any route; the runner and MCP server build on it |
| `@flyball/client` (`ui/packages/client`) | TypeScript | the same, typed from the wire format; the UI is built on it |
| `curl`, `requests`, a browser | anything | plain JSON over HTTP; downloads are plain `GET`s |

**Configure.** A URL (with the rig's root path if it has one) and, for a
rig with a sign-in, a named token as `Authorization: Bearer` (`flyball
token create` on the rig's host; a bare runner's own token for a bare
runner) -- a password is for people at the UI, not for code. `read` is
the default; ask for `operate` only for code that drives the rig.

**Everything else** is [The server](../4-server/index.md): the routes, the
wire format, errors, authentication.
