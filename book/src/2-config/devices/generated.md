# Where a device's options come from

Nothing on the [Supported drivers](drivers.md) page was written by hand
first and coded second. Each driver's fields *are* its config class -- a
pydantic model with a `type` -- and the same class is what validates the rig
file, draws the form in the UI, spells the CLI flags and describes the tool
a model calls. This page is how that works and where to look when a field
is not what you expected.

## One class, five faces

```python
class Sht4xConfig(DriverConfig[Sht4x], type="sht4x"):
    link: I2cLinkConfig | str
    address: int = Field(default=0x44, ge=0x03, le=0x77)
    precision: Literal["high", "medium", "low"] = "high"
```

| face | built from the class by | you see it as |
| --- | --- | --- |
| the rig file | `flyball.runtime.config` validating `devices:` against the driver catalog | `driver: sht4x` and its fields flat beside it; a wrong field fails `flyball rig check` with its name |
| the editor schema | `flyball rig schema` (`RigConfig.model_json_schema()`, every registered type folded in) | completion and red squiggles in `rig.yaml`, from the `# yaml-language-server: $schema=` line |
| the UI | `GET /api/drivers` (`schema` per type) → the discriminated-union form | the **Add device** dialog's fields, with defaults, bounds and choices |
| the CLI and the client | `GET /api/schema` and `/api/devices/{name}/schema` | `flyball device-schema <device>` and `flyball invoke <device> <command> …`, or a method per command on `flyball.interfaces.client.Rig` |
| a model | the MCP `author` tier's `attach_device` tool, whose argument schema is the same JSON | a tool call with the same fields |

The field's type sets the widget and the check (`int` with `ge`/`le` is a
bounded number; a `Literal` is a choice; a nested model is a sub-form); its
`Field(description=…)` is the hint text; its default is what the form
starts with and what the file may leave out. `Annotated[float, UnitRef(u)]`
puts a unit on the field, so a number is shown with it.

## The envelope is not the driver's

The keys every device shares -- `driver`, `label`, `poll_s`, `signals`,
`inputs` -- belong to the envelope ([Devices](index.md)), and a driver's
config may not declare a field by any of those names, nor `config`, which
an entry refuses: that is checked when the class is defined, so `signals:`
on a `scpi` device is always the signal metadata, never the query table (which is why that one is
called `channels`). Everything else in the entry is the driver's.

## Where a driver's tree comes from

A driver's *signals* are as generated as its config. A chip with a fixed
set of readings declares them as descriptors on the class
(`humidity = Readout("humidity", …)`); a generic driver builds them from its
own config at build time (`scpi`'s `channels`, `modbus`'s `registers`,
`i2c_table`'s `registers`). Either way the result is the same tree, and the
envelope's `signals:` metadata sits on top of it. What a driver may declare
is [The device model](../../3-extending/model.md); the two ways of declaring it
are [Writing a sensor](../../3-extending/device/sensor.md) and
[Config and build](../../3-extending/device/config.md).

## Links and laws are the same story

A link type ([Links](../links.md)) is a `Config[Link]` with a `type`; a law or a
feedforward ([Controllers](../controllers.md)) a `Config` again. All go through
the one registry, so `GET /api/drivers` lists links beside drivers and
`flyball rig schema` includes the laws.

## Where the class lives

| driver | class |
| --- | --- |
| `scpi` | `flyball_visa` |
| `modbus` | `flyball_modbus` |
| `qcodes`, `pymeasure` | `flyball_qcodes`, `flyball_pymeasure` |
| `sim_daq`, `sim_drive`, `sim_plant` | `flyball_sim.devices` |
| `sim_furnace` | `furnace.sim` (`examples/furnace`) |
| `visa`, `serial`, `fake_text` | `flyball_visa` |
| `modbus_tcp`, `modbus_rtu`, `fake_registers` | `flyball_modbus` |
| the board chips | `flyball_chips.*` |
| board-level Linux drivers and links | `flyball_linux.devices.*`, `flyball_linux.links.*` |
| a `drivers/` file, a package's | wherever it is: `GET /api/drivers` names the module per type |

A type registers when its module is imported: the built-ins on `import
flyball`, a package's through its `flyball.configs` entry point, a
`drivers/` file when the runner starts or `POST /api/drivers/reload` runs.
`flyball new NAME` writes a complete starting file.
