# A site: several sims on one origin

One daemon file per deployment. Each holds only `extends` (the rig it
serves) and a `daemon:` section (how): port, path prefix, what the API may
do, where the store goes. The rigs themselves are `examples/humidity` and
`examples/simulated`, unchanged.

| file | serves | at |
| --- | --- | --- |
| `humidity.yaml` | `../humidity/rig.yaml` + `sim.yaml` | `:8001`, under `/humidity` |
| `furnace.yaml` | `../simulated/furnace.yaml` | `:8002`, under `/furnace` |

Run each from the venv that has its drivers, then put a front on one port:

```
cd examples/humidity && uv run flyball-daemon ../site/humidity.yaml
cd controller        && uv run flyball-daemon ../examples/site/furnace.yaml
cd controller        && uv run python ../examples/site/front.py 8080 /humidity=8001 /furnace=8002
```

`front.py` is a development stand-in for nginx: it serves the built
dashboard (`ui/apps/dashboard/dist`, one build for every prefix) under each
prefix and passes that prefix's `/api`, `/ws`, `/mcp` and `/docs` to the
daemon on that port unchanged. Loopback only. In production the same three
`location` blocks per rig go in nginx -- see the book, *The daemon*, "A
sub-path" -- with `limit_except GET` on `/api/` for a rig the public may
watch but not drive.

Stores land in `stores/<rig name>.sqlite` (`store_dir`), gitignored.
