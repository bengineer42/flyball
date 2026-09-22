# The config file

!!! abstract "Where you are: Configuration"
    For the person **setting a rig up**: the file that says what is on the rig, what regulates what and how it is served. No code, only YAML.

    | if instead you want to… | go to |
    | --- | --- |
    | understand the words first | [Overview](../0-overview/index.md) |
    | operate a rig that is already set up | [Running a rig](../1-running/index.md) |
    | drive hardware nothing here supports, or add a law | [Extending](../3-extending/index.md) |
    | talk to a rig from your own code or a model | [The server](../4-server/index.md) |
    | change flyball itself | [Internals](../6-internals/index.md) |
    | look a key or a route up | [Reference](../7-reference/index.md) |
One YAML file (or TOML, or JSON) says everything about a rig and how it is
served. This part lays out every key, one page per section, in the order the
file is read:

| section | page | says |
| --- | --- | --- |
| top level | this page | the rig's name, what it builds on, the clock, recording |
| `runner:` | [The runner section](runner.md) | how the process serves: port, path, who may reach it, what the API may do, where files go |
| `links:` | [Links](links.md) | the buses, instrument connections and simulated plants devices are built on |
| `devices:` | [Devices](devices/index.md) | what is on the rig: the envelope every device shares, and per-signal overrides |
| | [Supported drivers](devices/drivers.md) | every driver's own fields, one section each |
| | [Where a device's options come from](devices/generated.md) | why those fields exist, and where else they appear |
| `controllers:` | [Controllers](controllers.md) | who regulates what, with which law |
| | [Boards and Linux I/O](boards.md) | board profiles and the Raspberry Pi links |
| | [Simulation](simulation.md) | plants, clocks and the overlay pattern |

The strict schema is the [rig file reference](../7-reference/rig-file.md);
an editor gets it with `flyball rig schema > rig.schema.json`.

```yaml
# yaml-language-server: $schema=rig.schema.json     # editor completion: `flyball rig schema > rig.schema.json`
name: furnace                  # top level: what this rig is called
extends: [base.yaml]           # optional: files this one builds on
board: pi4                     # optional: a board profile's links and pin labels
recording: false               # open a session at start?
clock: { speed: 60 }           # simulated rigs only

runner:                        # how the process serves; not part of the rig
  port: 8002
  root_path: /furnace
  allow_shutdown: true

links:                         # the buses and plants devices are built on
  tube: { tag: sim_furnace, zones: 2, power_w: 3000 }

devices:                       # what is on the rig, keyed by name
  furnace:
    driver: sim_daq
    label: Tube furnace
    poll_s: 1
    config: { link: tube, ports: { zone1: zone1 } }     # the plant's port, as the signal `furnace.zone1`
    signals:
      zone1: { warn: [0, 1100], precision: 1 }
  heaters:
    driver: sim_drive
    config: { link: tube, ports: { heater1: heater1 } }

controllers:                   # who drives what, keyed by the target signal
  heaters.heater1: { signal: furnace.zone1, law: { tag: PI, kp: 100, ki: 0.15 }, default: true }
```

Sections may be split across files -- `flyball-runner furnace.yaml sim.yaml`,
later files overlaying earlier -- or gathered into one with `extends`; see
[several files](#several-files). Unknown keys are refused at every level, so
a typo fails at load with its name; `flyball rig check FILE…` reports the
same without serving.

## Top level

| key | type | default | |
| --- | --- | --- | --- |
| `name` | string | the file's stem | the rig's name: in `/api/health`, session metadata, and the store's file name under `store_dir` |
| `extends` | `[path, …]` | none | files this one is layered on top of, relative to this file, in order |
| `board` | string | none | a board profile (a name on the board path, or a path): its `links` go under yours, and `pin: LABEL` on a device resolves against it -- [Boards](boards.md) |
| `recording` | bool | `false` | open a recording session when the runner starts (`--record` does the same once) |
| `clock` | `{speed, stepped}` | real time | `speed`: rig seconds per wall second; `stepped: true`: time moves only when stepped. Refused unless every link is `sim_*`/`fake_*` |
| `runner` | section | defaults | [The runner section](runner.md) |
| `links` | section | `{}` | [Links](links.md) |
| `devices` | section | `{}` | [Devices](devices/index.md) |
| `controllers` | section | `{}` | [Controllers](controllers.md) |

## Several files

The same keys in several files merge, later files winning, mappings key by
key and everything else whole; a `null` deletes what an earlier file set.
Three ways to lay a rig out:

```
flyball-runner rig.yaml sim.yaml            # the command line lists the layers
flyball-runner rig.yaml --set clock.speed=60  # a one-key overlay on top
flyball-runner site.yaml                    # one file that `extends` the rest
```

The conventional split is one file per concern: the hardware rig
(`links`, `devices`, `controllers`); a simulation overlay that swaps the
links and drivers under the same names (`examples/humidity/rig.yaml` +
`sim.yaml`, walked through in [the humidity book](https://bengineer42.github.io/humctrl/2-config/)); and a runner file per deployment that `extends` those and
carries only `runner:` (`examples/site/humidity.yaml`). Every address,
program, dashboard and session is then identical whether the rig is real
or simulated, and `flyball rig check FILE…` validates any combination
without serving it.

## Formats

A rig is a file; a program is a file. Either may be written in any of:

| suffix | reads with | best for |
| --- | --- | --- |
| `.toml` | `tomllib` (stdlib) | **rig config** — typed leaves, no `yes`/`no`/`1e3` surprises |
| `.yaml` | PyYAML (a strict loader that rejects duplicate keys) | **rig config and programs** — deep envelopes read better, and a program is a list you read like a script |
| `.json` | stdlib | anything a machine wrote: a UI's save, a generated program |

Meaning is decided after parsing, by the same models in every case, so the
three are interchangeable; the choice is only what is pleasant to write. YAML
is the documented form for rig files.

