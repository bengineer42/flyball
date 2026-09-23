# A site: several sims on one origin

One runner file per deployment. Each holds only `extends` (the rig it
serves) and a `runner:` section (how): port, path prefix, what the API may
do, where the store goes. The rigs themselves are `examples/furnace`,
unchanged, and [the humidity rig](https://bengineer42.github.io/humctrl/)
-- its own repo, cloned to `examples/humidity` alongside this one.

| file | serves | at |
| --- | --- | --- |
| `humidity.yaml` | `../humidity/rig-multi-sensor.yaml` + `sim.yaml` | `:8001`, under `/humidity` |
| `furnace.yaml` | `../furnace/rig.yaml` | `:8002`, under `/furnace` |

Run each from the venv that has its drivers, then put a front on one port:

```
cd examples/humidity && uv run flyball-runner ../site/humidity.yaml
cd examples/furnace  && uv run flyball-runner ../site/furnace.yaml
cd engine            && uv run python ../examples/site/front.py 8080 /humidity=8001 /furnace=8002
```

`front.py` is a development stand-in for nginx: it serves the built
dashboard (`ui/apps/dashboard/dist`, one build for every prefix) under each
prefix and passes that prefix's `/api`, `/ws`, `/mcp` and `/docs` to the
runner on that port unchanged. Loopback only. In production the same three
`location` blocks per rig go in nginx -- see the book, *The runner*, "A
sub-path".

A rig the public may watch but not drive is the runner's own business:
each file here sets `auth.anonymous: read` with a password, so anyone may
read and open the streams, and a login (the UI's page, or `POST
/api/auth/login`) is needed to do anything else. Put a real password in
before serving -- `flyball password` prints the hashed line -- or set
`FLYBALL_PASSWORD` in the environment. nginx's `limit_except GET` on
`/api/` still belongs in front of it as a second wall.

Stores land in `stores/<rig name>.sqlite` (`store_dir`), gitignored.
