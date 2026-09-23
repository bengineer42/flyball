# Writing a device driver for flyball

A device is a Python class with *descriptors* for its signals and, where it
does something, a `read` and/or a `commit`. A *driver config* beside it
registers a type so a rig file can say `driver: <type>`. Check what you wrote
with `check_driver`, attach it with `attach_device`, keep it with `save_rig`.

## First: does it need code at all?

Probably not. Two generic drivers take everything from the rig-file entry:

- `scpi` -- a text instrument over VISA or serial: each signal is a query
  string (and a write string for a demand). `link_query` with `*IDN?` tells
  you what is there; try a candidate query the same way.
- `modbus` -- registers over TCP or RTU: each signal is an address, a type
  and a scale.

`list_drivers` shows every type the runner has and each config's schema;
`rig_schema` shows the whole file. Write the entry, `check_rig` the file,
`attach_device` the entry, `read` its signals. Only reach for code when the
protocol is neither, or the device does arithmetic across its signals (a
blender that mixes two pumps to a humidity is code).

## The shape of a driver

```python
"""Thermo Foo 200: a two-channel bath over serial."""

from __future__ import annotations

from collections.abc import Iterator

from flyball.foundation.device import (
    Committable,
    Demand,
    DriverConfig,
    Node,
    Readable,
    Readout,
    Sample,
    command,
)
from flyball.foundation.quantities import Quantity
from flyball.foundation.quantities.si import Celsius
from flyball.hardware.links import TextLink
from flyball_visa import TextLinkConfig  # a link config, or a str name

TEMP = Quantity("temperature", Celsius)


class Foo200(Readable, Committable):
    """What it measures and drives, in one line; the rest of the docstring is for people."""

    bath = Readout("bath", "Bath temperature", TEMP, range=(-20.0, 200.0), precision=2)
    setpoint = Demand("setpoint", "Bath setpoint", TEMP, limits=(-20.0, 200.0))

    def __init__(self, name: str, link: TextLink, label: str | None = None) -> None:
        super().__init__(name, label)
        self._link = link

    def read(self, time_ns: int, node: Node | None = None) -> Iterator[Sample]:
        """Called every `poll_s`. Yield one Sample per instant read; raise HardwareError to go offline."""
        yield self.sample(time_ns, bath=float(self._link.query("READ?")))

    def commit(self, time_ns: int) -> None:
        """Everything recorded since the last commit, to the hardware, once."""
        if (target := self.setpoint.staged) is not None:
            self._link.write(f"SET {target:.2f}")

    @command
    def stop(self) -> None:
        """Something an operator or a program runs; does its own I/O at once."""
        self._link.write("STOP")


class Foo200Config(DriverConfig[Foo200], type="foo200"):
    """The rig-file entry: `driver: foo200`, its fields flat beside it."""

    link: TextLinkConfig | str  # a link declared under `links:` by name, or inline

    def build(self, name: str, label: str | None = None) -> Foo200:
        if isinstance(self.link, str):
            raise TypeError(f"link {self.link!r} must be resolved before building")
        return Foo200(name, self.link.build(), label)
```

`driver_scaffold` gives a smaller version of this that already runs.

## Descriptors

| descriptor | what | who sets it |
|---|---|---|
| `Readout(name, label, quantity, range=, precision=, warning=, alarm=)` | a value the device produces | the driver, by `push` or in a `Sample` |
| `Demand(name, label, quantity, limits=)` | a value someone asks for; its readback is what the device is doing | a controller, a command, `set_demand` |
| `Namespace(name, label)` then `ns.readout(...)` / `ns.demand(...)` / `ns.config(...)` / `ns.input(...)` | a subtree, one address segment | -- |
| `ns.config(name, label, quantity)` | a value fixed at build from the config (a max flow) | `build`, by `self.x.push(...)` |
| `ns.input(name, label, quantity, default=)` | another device's signal the rig binds to this input (`inputs:` in the rig file) | the rig |
| `tags={"line": "dry"}` on any descriptor | a grouping across the tree (`dry`/`wet`), never part of the address | -- |

Rules: `name` is the address segment, `label` the display text. `limits`
may be numbers, a config descriptor (resolved at build) or an input
(resolved live). An `Readout` with a non-float `vtype` (an enum, `initial=`)
is how a device reports its mode. On the class a descriptor is the spec; on
the instance `self.bath` is the bound signal: `.value`, `.push(value,
time_ns)`, and for a demand `.staged` (what was asked and not yet
committed) and `.at_limit`.

## Read and commit

- `Readable` ⇒ implement `read(time_ns, node)`. Yield `self.sample(time_ns,
  **values)` or `Sample(self.root, time_ns, {self.signals["x"]: v})`.
  Strings never reach a driver: keys are bound signals. Yield nothing when
  nothing is due. Raise `flyball.foundation.errors.HardwareError` to go offline;
  the rig records the condition and retries next poll.
- `Committable` ⇒ implement `commit(time_ns)`. The rig has already
  validated and clamped every demand and recorded it under `pending`; the
  default `commit` calls `write_signal(signal, value)` per pending signal,
  so a simple actuator overrides only that. A composite device overrides
  `commit`, reads its inputs (`self.dry_supply.value`), writes once, and
  pushes its readbacks. It is called once per delivery however many demands
  and inputs arrived together, and at once after a manual demand.
- Any demand, or any command with `commit=True`, requires `commit`; any
  output requires a `read` or pushes from `commit`. Checked at class
  creation.

## Commands

`@command` on a method makes it runnable by people and programs (and by an
MCP `<device>-<method>` tool). It does its own I/O at once. Options:
`@command(mode=Mode.MANUAL, interrupts=True)` says what the device's `mode`
output becomes and that a controller driving the device is put in manual
first; `commit=True` for a command that only records and lets `commit` do
the I/O; `simulation=True` for a command that exists only on a simulated
rig. A parameter named exactly like a demand descriptor is linked to it:
its schema takes that demand's unit and limits, and left out it takes the
current readback. Every scalar demand no command links to gets a
synthesised `set_<name>`. Docstrings become the tool descriptions: first
line what it does, then what the arguments mean.

## The config and the link

`DriverConfig[Device]` with `type="..."` is a pydantic model: its fields are
the rig-file settings, validated and schema-published. It may not use an
envelope key (`driver`, `label`, `poll_s`, `signals`, `inputs`) or `config`.
`build(name, label)` makes the device. A transport is a *link*: declare
the field as `SomeLinkConfig | str`; a string names an entry under
`links:` that the rig resolves to the config before `build`. Existing
links: `visa`, `serial`, `fake_text` (text), `modbus_tcp`, `modbus_rtu`,
`fake_registers` (registers), and on Linux `i2c`, `pwm`, `fake_pwm`. The
fakes are how a driver is tested without hardware.

## Getting it onto the rig

1. Write the module where the runner runs, in its drivers directory.
2. `check_driver(path)`: imports it in a fresh interpreter and reports the
   type, the config schema, the device's descriptors and commands, or the
   error.
3. `reload_drivers`, then `list_drivers` shows the type.
4. `attach_device(name, entry)` with `{"driver": "<type>", "link": "...",
   ...}`; `read` or `view_device` to see it live; `set_demand` or its
   commands to drive it.
5. `save_rig` writes the entry into the rig file so it survives a restart.

A driver package that ships is registered with an entry point instead:
`[project.entry-points."flyball.configs"] name = "package.module"`.
