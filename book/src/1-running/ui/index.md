# The UI

The dashboard app (`ui/apps/dashboard`) is a pure function of what the server
publishes: schema, telemetry and history over HTTP and websockets, plus
layout it authors itself and the server stores. It knows nothing about any
particular quantity — adding a device in Python produces a working page
with no front-end change. This section is what the app does, a page per area:

| page | |
| --- | --- |
| [Devices](devices.md) | a card per device: signals, commands, conditions; adding and removing devices and links |
| [Controllers](controllers.md) | the faceplate: reading, target, output, trends, the law |
| [Charts and the Graph page](charts.md) | Inputs, Graph, chart controls, keyboard shortcuts, downloads, stale tiles |
| [Sessions](sessions.md) | recording, the rolling record, keep, pin, export, delete |
| [The Rig page](rig.md) | the running document, versions and restore, save, restart, connect a model |
| [Dashboards](../dashboards.md) | saved and generated layouts of widgets |

The visual language is *Design rationale* below and `ui/README.md`.

## Pages

| Page | For |
| --- | --- |
| **Dashboards** | saved and generated layouts of widgets — see [Dashboards](../dashboards.md) |
| **Overview** | the rig at a glance: a stat tile per publishing signal, a card per polled device (its run: period, last read, conditions) |
| **Inputs** | every publishing signal charted, grouped by device or by unit |
| **Devices** | one card per device (signals, commands, conditions); add or remove a device or a link |
| **Controllers** | one card per writable signal: with a controller the card is the faceplate, its device's other signals and commands open inline below; without one, the signal's card alone plus an "Add controller" button. `#/loops` and `#/actuators` redirect here |
| **Programs** | the program library (check, run, delete, upload, new) and, for a running or past program, its steps and events |
| **Events** | the rig's event log, live, filterable by level |
| **Sessions** | start/stop recording, list recorded sessions and the runner's rolling buffer(s) (if it keeps one) in their own table, keep a range as a session or forget it outright, pin, open a session and rename it, export, delete |
| **Rig** | the running rig as a file would show it, what has changed since the runner started, its version history (the current one marked), saving it, connecting a model over MCP, and — when the runner allows — restarting or shutting it down |
| **Simulation** | simulation-only controls: clock speed, each plant's live parameters, and per-device faults (`fail`, `restore`, `disturb`, `set_limits`) — these never appear on a controller's device section |

## The app bar

A condition summary sits in the app bar, built from `/api/health` (falling
back to a client-side count from the samples stream on an older runner):

- an always-present **alarm** chip — the count of active device conditions
  plus signals outside their warn/alarm bands, coloured by the worst one
  (amber for warn, red for alarm; otherwise the neutral outline every
  healthy state uses — colour is reserved for abnormal conditions), held to
  one width across its own states so it doesn't reflow its neighbours as the
  count changes;
- a **server** chip folding every websocket the page has open into two
  states, not three: green when every stream is open, red otherwise — a
  dropped stream is shown as reconnecting (not yet red) for a few seconds
  before it escalates, so a brief reconnect doesn't read as an outage.
  Hovering names which stream (readings, controllers, waits, events) is the
  problem, when it's known;
- a **recording** dot (a filled circle in a ring, the standard record
  symbol): the open session's name, or "not recording";
- a **program** chip, only while one is running: its name and step;
- a **devices** chip, only while some polled devices are not running: `n/total`;
- a **sim** chip, only when the simulated clock is not at ×1: its speed.

Every chip's tooltip lists the names behind the count (conditions, devices)
and links to the page that explains it (Events, Sessions, Programs, Devices,
Simulation).

### The playback bar

On a simulated rig, a second, slim row appears under the app bar once the
open session has some history: a video-style transport (rewind, play/pause,
fast-forward, a scrub slider from the session's start to now) over that
session's recorded samples, read from `/api/history`. Scrubbing back or
pausing only changes what the bar reports — it never writes a demand or a
setpoint, so it is read-only by construction; resuming (the play button, or
fast-forwarding past now) goes straight back to live. Hidden on a real rig,
and on a simulated one until there is a session with some history to scrub.

## Density and theme

Two toggles in the app bar, both persisted to `localStorage` and applied as
attributes on `<html>` so the whole app (MUI and the plain-CSS `packages/react`
components alike) reads them from the same CSS custom properties:

- **Theme** (`flyball.theme`): light/dark, defaulting to the OS preference
  (`prefers-color-scheme`) until chosen explicitly (`data-theme`).
- **Density** (`flyball.density`): comfortable/compact (`data-density`),
  changing tile gaps, a tile's title-row height and a readout's minimum
  height.

## Signing in

A runner with a password (or a token -- [the
door](../runner/access.md#the-door-a-password-a-token-or-open)) shows a
**Sign in** page instead of the app until the browser has a session: one
field, a wrong password said inline, ten wrong ones in a minute refused for
the rest of it. A successful login is a cookie the runner sets and the
browser carries by itself on every request, socket and download; the app
keeps nothing, so there is no token to find in its storage or in a copied
link. The session lasts as long as the runner says (`auth.session`, twelve
hours by default); when it ends, the next refusal brings the page back.

Signed in, a **signed in** chip sits in the app bar beside the other status
chips; its menu has **Sign out**. On a runner anyone may look at
(`auth.anonymous: read`) the app opens without a login and the chip says
**read only**; a control that needs a login is refused with a nudge to
sign in, and the chip (or the nudge) leads to the page, which has **Keep
looking** to come back without one.

`?token=…` on the page's own URL -- how a runner's token used to be handed
to a browser -- still works: it is signed in with once and dropped from the
visible address, so it is not left in history.

## Design rationale

Colour is reserved for abnormal states (ISA-101): a normal reading is
neutral, warn and alarm change the tile's border and never only its colour,
and a stale tile is dashed with a hollow status dot. Every colour, space,
radius and duration is a token in `ui/packages/react/src/styles.css`
(`ui/README.md` *Theming* lists them with their purpose and contrast).
