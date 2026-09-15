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
| `Duration \| Rate` (a pace) | either form; **may be written flat** beside the other arguments when it is the command's only time field |
| `ValueSource \| float` | a number, or `process`, `setpoint`, `demand` |
| `Channel` | `{source: name, measurand: name}` |
| a law (`tuning`) | the name of a registered tuning, or `{tag: PI, kp: …, ki: …, tt: …}` |
| `Transfer` | `track`, `carry`, `reset`, `none` |

## Library commands

| tag | field | type | default |
| --- | --- | --- | --- |
| `regulate` | `loop` | string | — |
| | `at` | `ValueSource \| float` | — |
| | `generator` | trajectory | none |
| | `tuning` | law | keep the current |
| | `transfer` | `Transfer` | `track` |
| `linear_ramp` | `loop` | string | — |
| | `end` | number | — |
| | `pace` | `Duration \| Rate` | — |
| | `start` | `ValueSource \| float` | `process` |
| `update_setpoint` | `loop` | string | — |
| | `value` | number | — |
| `settle_above` | `channel` | `Channel` | — |
| | `above` | `ValueSource \| float` | — |
| | `margin` | number | 0 |
| | `duration` | `Duration` | — |
| | `readings` | integer ≥ 1 | 1 |
| | `timeout` | number or null | — |
| `settle_below` | as `settle_above`, with `below` | | |
| `settle_at` | as `settle_above`, with `at` and `tolerance` | | |
| `wait` | `message` (primary) | string | — |
| | `timeout` | `Duration` | none |

!!! note
    `flyball.programmer.commands`, which defines all but `wait`, does not
    currently import. The table is the vocabulary as written.

## The generated schema

`program_schema(dialect)` emits the JSON Schema for the whole file from the
command registry: one `oneOf` branch per command, each requiring its key;
the value is the command's request schema without `command`, or the bare
`primary` field's schema as an alternative; time fields gain their flat keys;
modifier keys are allowed on every branch. Point an editor at it with

```yaml
# yaml-language-server: $schema=./program.schema.json
```
