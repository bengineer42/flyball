# The UI

The UI is a pure function of what the server publishes. It knows nothing
about any particular quantity: adding an actuator in Python produces a
working panel with no front-end change.

## What it reads

| Input | Says | From |
| --- | --- | --- |
| **schema** | what exists: sources, channels, actuators, their config/state/command schemas, loops, tunings | `GET /api/schema` and `/api/loops` |
| **telemetry** | samples, loop ticks, actuator and reader state, signals | the websockets |
| **history** | series, ticks, events, spans for a session | `GET /api/history/…` |
| **layout** | what to show and how | authored in the UI, stored on the server |

## Panel kinds

| Kind | Shows |
| --- | --- |
| `graph` | one or more channels over time, with a loop's setpoint and output overlaid; pan back from live into history and forward again |
| `gauge` / `readout` | one channel or state field: value, unit, range |
| `actuator` | an actuator's state, schema-labelled, and one form per command |
| `loop` | setpoint, output, law state, tuning selector |
| `form` | any JSON schema in, validated JSON out; the component every other panel uses for input |
| `program` | a step editor driven by the program schema, with the YAML alongside |
| `events` | a session's events and spans on a timeline |
| `sessions` | start, stop, name, list, delete, export |

The form is the keystone. Widget choice follows the schema: bounds give a
slider, `enum` with per-option titles a select, a discriminated `oneOf` a
kind picker, `unit` a suffix. Nothing else is needed to render any command,
config, tuning or layout panel.

## Reading a loop panel

- **reference** — where the loop is aiming; a number, or a ramp in progress.
- **reading** — the last value on the loop's channel.
- **correction** — what the law added to the setpoint.
- **demand** — `setpoint + correction`, what the actuator was told.
- **expected** — what the actuator said it would deliver; differs from demand
  when it is railed.
- **mode** — `manual`, `open` or `regulating`.

## Status

The front end lives in `ui/` as an npm workspace: `@flyball/client` (wire
types, transport interface, `RigClient`; no framework), `@flyball/react`
(hooks, the schema form, panels) and a dashboard app that is the smallest
consumer of the two. Today it renders one `actuator` panel per actuator from
`GET /api/schema`, with live state from `/ws/actuators` and every command as
a generated form. The server does not yet serve the built bundle; in
development Vite proxies `/api` and `/ws` to the daemon. The design is
recorded in the repository's `UI.md`.
