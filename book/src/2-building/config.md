# Config and build

A **config** is a description that builds something: a pydantic model with
`build() -> T`. It is the *config* tier of a device, and it is how a rig is
eventually described in a file rather than in code.

```python
--8<-- "device.py:11:18"
```

## Why a model, not a dataclass

Config arrives from outside — a YAML file, a `PUT` request — so it is
validated on the way in. Pydantic gives that, plus a JSON schema the UI and
CLI build forms from. Settings and state are dataclasses because they are
built in-process and only ever leave.

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

class InstrumentConfig(Config[Instrument]):
    link: Config.union(SerialLink, VisaLink)
```

A file then says `link: {tag: visa, address: "GPIB0::12"}`, and the schema
says exactly which fields follow `tag: visa`. Tags are one namespace across
the process; a clash is an error at class definition.

## `ConfigOr`

Any component may be given either a built object or a config for one:

```python
def attach(link: ConfigOr[Link]) -> None:
    link = resolve(link)   # builds it if it is a config, else returns it
```

That is what lets the same constructor serve code and files.

## In a rig file

A config with a `tag` is what a rig file names. `flyball.runtime.config`
validates a file of links, readers, actuators and loops against the tag
registries and builds the rig:

```toml
[[actuators]]
tag = "scpi_actuator"
name = "psu"
link = "bench"
command = "SOUR:VOLT {value}"
demand_unit = "V"
```

```python
from flyball.runtime.config import load_rig
rig = load_rig("rig.toml")
```

The file's unions admit the generic devices and links; a device of your own
is not nameable from a file until it is added to them. See
[Rig file schema](../6-reference/rig-file.md).

## The device's side

The device keeps the config it was built from and returns it from `config`.
Rebuilding is the only way to change it; the loop that drives an actuator
never sees its config.

```python
--8<-- "device.py:40:46"
```

```python
--8<-- "device.py:55:57"
```
