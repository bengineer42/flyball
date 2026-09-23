# Program file schema

A program file is a mapping with `name` and `steps`. Loaded from `.yaml`,
`.json` or `.toml`; the dialect is the same in each.

```yaml
name: string            # required
steps:                  # required, at least one
  - <tag>: <arguments>
    <modifier>: <value> # optional, application-defined
```

## A step

Exactly one command key, plus any modifier keys the application's dialect
declares. Any other key is an error.

| value under the command key | means |
| --- | --- |
| a mapping | the command's arguments, verbatim |
| a scalar or a list | the command's `primary` field; an error if it has none |

## Argument types

| type | written as |
| --- | --- |
| number, string, bool | as in YAML |
| `Duration` | `{seconds: 90}`, `{minutes: 1, seconds: 30}`, `{hours: 2}`, or a bare number of seconds. Keys are the plural of `nanosecond`, `microsecond`, `millisecond`, `second`, `minute`, `hour`, `day`; they add |
| `Rate` | one key: `{per_second: 0.01}`, `{per_minute: 2}`, … |
| `Duration \| Rate` (a pace) | either form; **may be written flat** beside the other arguments when it is the command's only such field, `timeout` aside (`foldable()` in `flyball.interfaces.server.dialect`). `timeout` is always a `Duration` named `timeout` and is never itself a fold candidate; a step whose only time field is `timeout` (`prompt`, `settle`) takes no flat keys. `wait`'s `duration` folds flat only when the step has no `message`: `wait: {minutes: 20, message: "…"}` is refused (that is what an old operator prompt with a flat timeout looked like) -- write `wait: {duration: {minutes: 20}, message: "…"}` |
| `ValueSource \| float` | a number, or `measured`, `setpoint`, `output` |
| a controller name | the address of the demand it drives, e.g. `heaters.heater1` — a controller is named by its output |
| a law (`tuning`) | the name of a registered tuning, or `{tag: PI, kp: …, ki: …, tt: …}` |
| `Transfer` | `track`, `carry`, `cold`, `none` |

## The library's commands

Every rig has these; `loop` (kept as the field name — a controller is what
today's `Loop` is called, but the argument is unchanged) is one address, a
list of addresses, or omitted for the rig's default controller. Source:
`flyball.sequencing.{loops,devices,activities}`.

| tag | field | type | default |
| --- | --- | --- | --- |
| `regulate` | `setpoint` (primary) | number | — |
| | `loop` | address, list, or omitted | rig default |
| | `tuning` | law name | keep the current |
| `ramp` | `to` (primary) | number | — |
| | `pace` | `Duration \| Rate`, foldable | — |
| | `loop` | address, list, or omitted | rig default |
| | `wait` | bool | `true` |
| `wait` | `duration` (primary), foldable without `message` | `Duration` | — |
| | `message` | string | none |
| | `timeout` | `Duration` | none |
| `settle` | `loop` (primary) | address, list, or omitted | rig default |
| | `within` | number | `1.0` |
| | `count` | integer ≥ 1 | `3` |
| | `timeout` | `Duration` | none |
| | `message` | string | none |
| `manual` | `loop` (primary) | address, list, or omitted | rig default |
| `set` | `device` | name | — |
| | `values` | `{name: value}` | — |
| `command` | `device_command` | tag | — |
| | `device` | name | — |
| | `args` | `{name: value}` | none |
| `prompt` | `message` (primary) | string | — |
| | `name` | string | `"prompt"` |
| | `timeout` | `Duration` | none |

`regulate`/`ramp`/`wait`/`settle`/`manual` are steps on a **controller**
(named by its target's address); `set` and `command` reach a **device**
directly — `set` is one demand (`rig.write`) on its writable signals,
`command` calls one of its `@command` methods, `device_command` naming the
tag rather than `command` because a step's own wire form reserves
`command` for its own tag. See [Programs](../1-running/programs/index.md) for
the concepts and [Writing programs](../1-running/programs/writing.md) for the full
worked example.

## The generated schema

`program_schema(dialect)` (`flyball.interfaces.server.dialect`) emits the JSON Schema
for the whole file from the command registry: one `oneOf` branch per
command, each requiring its key; the value is the command's request schema
without `command`, or the bare `primary` field's schema as an alternative;
foldable time fields gain their flat keys; modifier keys are allowed on
every branch. Point an editor at it with

```yaml
# yaml-language-server: $schema=./program.schema.json
```

`GET /api/programs/schema` serves it for the rig's own dialect;
`flyball program schema` writes it to a file.
