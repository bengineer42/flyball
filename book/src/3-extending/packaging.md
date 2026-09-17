# Packaging

Three ways a driver, a link or a law you wrote reaches a rig file, from the
quickest to the most shareable.

## A file in `drivers/`

Drop `mydriver.py` into the `drivers/` directory beside the rig file (or
the one the [`daemon:` section](../2-config/daemon.md) names). The daemon
imports every `.py` there at start and again on `POST /api/drivers/reload`,
so a driver written on the spot -- by hand, or by a model over MCP -- is
attachable without a restart or a package. `GET /api/drivers` lists what
registered and any import error. `flyball new NAME` writes a complete
starting file with a tag.

## A package with an entry point

For a driver used by more than one rig, a distribution that declares its
config modules under the `flyball.configs` entry point:

```toml
[project.entry-points."flyball.configs"]
keithley = "flyball_keithley.configs"
```

After `pip install`, its tags are valid in any rig file, `flyball rig check`
and `flyball rig schema` know them, and the UI's Add-device form offers
them. `flyball-linux` is the first such package; `examples/humidity` the
second (a whole application: drivers, rig files, programs, dashboards, a
book). What to put in one: [Config and build](device/config.md) for the
config class; [Where a device's options come from](../2-config/devices/generated.md)
for what the class produces.

## Inside the application

A device whose only home is one rig's own package needs neither: the
application's daemon (its own entry point around [`serve`](rig.md#serving))
imports it, and the tag registers on import.

## The example applications

`examples/humidity` is the template for a package: `pyproject.toml` with the
entry point and `flyball` / `flyball-linux` as dependencies, `src/humidity/`
(`blender.py`, `sim.py`, `cli.py`), `rig.yaml` + `sim.yaml`, `programs/`,
`tunings/`, `book/`. Copy its shape.
