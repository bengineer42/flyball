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
| **Sessions** | start/stop recording, list recorded sessions (and the runner's rolling record, if it keeps one), keep a range of it, pin, open one, export, delete |
| **Rig** | the running rig as a file would show it, what has changed since the runner started, its version history (the current one marked), saving it, connecting a model over MCP, and — when the runner allows — restarting or shutting it down |
| **Simulation** | simulation-only controls: clock speed, each plant's live parameters, and per-device faults (`fail`, `restore`, `disturb`, `set_limits`) — these never appear on a controller's device section |

## The app bar

A condition summary sits in the app bar, built from `/api/health` (falling
back to a client-side count from the samples stream on an older runner):

- an always-present **alarm** chip — the count of active device conditions
  plus signals outside their warn/alarm bands, coloured by the worst one
  (amber for warn, red for alarm; otherwise the neutral outline every
  healthy state uses — colour is reserved for abnormal conditions);
- a **stream health** dot (`live` / `reconnecting` / `offline`) folding
  every websocket the page has open;
- a **recording** dot: the open session's name, or "not recording";
- a **program** chip, only while one is running: its name and step;
- a **devices** chip, only while some polled devices are not running: `n/total`;
- a **sim** chip, only when the simulated clock is not at ×1: its speed.

Every chip's tooltip lists the names behind the count (conditions, devices)
and links to the page that explains it (Events, Sessions, Programs, Devices,
Simulation).

## Density and theme

Two toggles in the app bar, both persisted to `localStorage` and applied as
attributes on `<html>` so the whole app (MUI and the plain-CSS `packages/react`
components alike) reads them from the same CSS custom properties:

- **Theme** (`flyball.theme`): light/dark, defaulting to the OS preference
  (`prefers-color-scheme`) until chosen explicitly (`data-theme`).
- **Density** (`flyball.density`): comfortable/compact (`data-density`),
  changing tile gaps, a tile's title-row height and a readout's minimum
  height.

## Bearer token

A **token** chip sits in the app bar beside the other status chips (a key
icon; click it for a small form with one field). It holds the runner's
bearer token — see [the runner's "The token"](../runner/access.md#the-token) for what
that gets a client and what it does not — kept in this browser's
`localStorage` (`flyball.token`) so it survives a reload, and taken once
from `?token=…` on the page's own URL if it is there (then dropped from the
visible address, so it is not left in history or in a copied link). The
client puts it on every `/api` request as `Authorization: Bearer …` and on
every `/ws` URL as `?token=…`, the one place a browser cannot set a header;
changing it rebuilds the client and reconnects every socket at once.

Without a token, or the wrong one, the runner's first refusal — `GET
/api/devices`, the very first thing the app asks for — replaces the whole
page with "This rig needs a token" and the same field, front and centre
rather than left for a person to go hunting for the small chip.

## Design rationale

Colour is reserved for abnormal states (ISA-101): a normal reading is
neutral, warn and alarm change the tile's border and never only its colour,
and a stale tile is dashed with a hollow status dot. Every colour, space,
radius and duration is a token in `ui/packages/react/src/styles.css`
(`ui/README.md` *Theming* lists them with their purpose and contrast).
