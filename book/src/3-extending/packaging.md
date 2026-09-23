# Packaging

Three ways a driver, a link or a law you wrote reaches a rig file, from the
quickest to the most shareable.

## A file in `drivers/`

Drop `mydriver.py` into the `drivers/` directory beside the rig file (or
the one the [`runner:` section](../2-config/runner.md) names). The runner
imports every `.py` there at start and again on `POST /api/drivers/reload`,
so a driver written on the spot -- by hand, or by a model over MCP -- is
attachable without a restart or a package. `GET /api/drivers` lists what
registered and any import error. `flyball new NAME` writes a complete
starting file with a type.

## A package with an entry point

For a driver used by more than one rig, a distribution that declares a
`flyball.configs` entry point pointing at a module with a
`register(catalog)` function:

```toml
[project.entry-points."flyball.configs"]
keithley = "flyball_keithley.configs"
```

```python
# flyball_keithley/configs.py
from flyball.model.catalog import Catalogs

from ._config import KeithleyConfig


def register(catalog: Catalogs) -> None:
    catalog.register_device(KeithleyConfig)
```

Registering is explicit, not a side effect of the module being imported:
`flyball-runner` builds one [`Catalogs`](../6-internals/decisions.md), calls
`discover()` -- which walks every installed package's entry point and calls
its `register(catalog)` -- once at startup. After `pip install`, its tags
are valid in any rig file, `flyball rig check` and `flyball rig schema`
know them, and the UI's Add-device form offers them. `flyball-linux` is the
first such package; `examples/humidity` the second (a whole application:
drivers, rig files, programs, dashboards, a book). What to put in one:
[Config and build](device/config.md) for the config class;
[Where a device's options come from](../2-config/devices/generated.md) for
what the class produces. A link is `catalog.register_link(...)` instead of
`register_device`; a law, a feedforward or a setpoint generator has its own
`register_law`/`register_feedforward`/`register_generator`; a program step
(a `Step` subclass) has `register_step`.

## Inside the application

A device whose only home is one rig's own package needs neither: the
application's runner (its own entry point around [`serve`](rig.md#serving))
calls `Catalogs().discover()` the same way `flyball-runner` does, or builds
its `Catalogs` by hand and registers the device directly.

## The example applications

`examples/humidity` is the template for a package: `pyproject.toml` with the
entry point and `flyball` / `flyball-linux` as dependencies, `src/humidity/`
(`blender.py`, `sim.py`, `cli.py`), `rig.yaml` + `sim.yaml`, `programs/`,
`tunings/`, `book/`. Copy its shape.
