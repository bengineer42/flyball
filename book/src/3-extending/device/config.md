# Config and build

A **config** is a description that builds something: a pydantic model with
`build() -> T`. It is the *config* tier of a device, and it is how a rig is
eventually described in a file rather than in code.

```python
--8<-- "device.py:heater-config"
```

A device's config is a `DriverConfig[D]` — `Config[D]` with `build(name,
label)` instead of a bare `build()`, since the envelope (the rig file's
`driver:`/`label:`/`poll_s:`/`signals:`/`bound:` keys, the same for every
driver) supplies the name. A link's config is a plain `Config[Link]`; a law's
or a feedforward's config the same shape again. `runtime.config.role_of()`
tells the three apart by what they build.

## Why a model, not a dataclass

Config arrives from outside — a YAML file, a `PUT` request — so it is
validated on the way in. Pydantic gives that, plus a JSON schema the UI and
CLI build forms from. A device's other signals (`Readout`, `Setting`,
`Demand`) are readings in the router, not a returned model: they are built
in-process and only ever leave.

Configs hold real defaults rather than `None` sentinels, so a serialised
config records what the rig actually did. `model_dump(exclude_unset=True)`
recovers what the user wrote when that is what is wanted.

## Tags and unions

Where a field admits several implementations — a pump driver, an instrument
link — each config declares a `tag`, and `Config.union` gives the
discriminated union a file validates against:

```python
class SerialLink(Config[Link], tag="serial"): ...
class VisaLink(Config[Link], tag="visa"): ...

class InstrumentConfig(DriverConfig["Instrument"]):
    link: Config.union(SerialLink, VisaLink)
```

A file then says `link: {tag: visa, address: "GPIB0::12"}`, and the schema
says exactly which fields follow `tag: visa`. Tags are one namespace across
the process — a [Catalog][flyball.model.catalog.Catalog] per kind, held in
[Catalogs][flyball.model.catalog.Catalogs] — so `driver:`, a link's `kind:`,
a law's `kind:` and a program step's tag each collision-check against their
own catalog, explicitly, through a `register(catalog)` entry point rather
than as a side effect of importing the module.

## Envelope keys are reserved

`driver label poll_s signals bound config` belong to the rig file's
envelope, the same for every driver (§1.5 of the plan). A `DriverConfig`
subclass may not declare a field with one of those names — checked at
import, the same way a duplicate tag is — so a driver's own settings mean
the same thing whether they sit flat beside the envelope or nested under
`config:`:

```yaml
devices:
  furnace: { driver: sim_daq, label: Tube furnace, poll_s: 1, link: tube, ports: {...} }   # flat

  furnace:                                                                                  # layered
    driver: sim_daq
    label: Tube furnace
    poll_s: 1
    config: { link: tube, ports: {...} }
```

This is why a generic driver whose tree is *part of* its config (`scpi`'s
`channels:`, `sim_daq`'s `ports:`, `sht4x_set`'s `sensors:`) cannot call
that field `signals:` — that name is the envelope's, for overrides only.

## `ConfigOr`

Any component may be given either a built object or a config for one:

```python
def attach(link: ConfigOr[Link]) -> None:
    link = resolve(link)   # builds it if it is a config, else returns it
```

That is what lets the same constructor serve code and files.

## In a rig file

A `DriverConfig` with a `tag` is what a rig file's `devices:` section names.
`flyball.runtime.config` validates a file's `links`, `devices` and
`controllers` against the tag registries and builds the rig — see
[Rig file schema](../../7-reference/rig-file.md) for every field and
[Assembling a rig](../rig.md#from-a-file) for loading one.

## The device's side

The device keeps the config it was built from and returns it from `config`.
Rebuilding is the only way to change it; a controller driving the device
never sees its config.

```python
--8<-- "device.py:heater-init"
```
