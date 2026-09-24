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
| [Charts and the Graph page](charts.md) | Readings, Graph, chart controls, keyboard shortcuts, downloads, readings with no value, stale tiles |
| [Sessions](sessions.md) | recording, the rolling record, keep, pin, export, delete |
| [The rig file](rig.md) | Options › Rig file: the running document, versions and restore, save, restart, connect a model |
| [Dashboards](../dashboards.md) | saved and generated layouts of widgets |

The visual language is *Design rationale* below and `ui/README.md`.

## Getting around

There is no sidebar: the app bar at the top of every page is how you move.

```
 ⌂  Overview (generated) · furnace · wall  [+]     ⚠ 0 conditions ³  ⏺ session #4  ▷ no program  ✓ server  ⚗ sim   [■ Software stop]  ⚙
```

- **⌂ goes back to the dashboards** from any page (the home dashboard, if
  one is set, else the generated overview).
- **Dashboards are tabs.** On the dashboards page the bar shows one tab per
  saved dashboard, after the generated overview; `[+]` makes a new, empty
  one. A tab shows the dashboard's label and links by its name
  (`#/dashboards/<name>`). On a phone the tabs sit
  on a row of their own, under the chips. On any other page the bar shows
  that page's title.
- **The chips are the way to the running pages.** Each is always there, grey
  when nothing is happening: the conditions chip opens **Events** (and
  carries the count of unread warnings), the recording chip **Sessions**,
  the program chip **Programs**; on a simulated rig the sim chip opens
  **Simulation**. The server chip says whether the live streams are
  connected.
  On a phone the chips move to a second row of the bar, shorter and
  wrapping, so every one stays in sight.
- **Software stop** keeps its place whether or not you may operate, so
  signing in does not move anything.
- **Without `operate`, every write control is shown but off**: run, save,
  delete, rename, import, pin, keep, start or end a recording, add or remove
  a device, link or controller, restore a version, restart the runner. A
  device, link or controller chip loses its remove cross. Opening **Programs** as a
  reader lists the library without importing new files from the programs
  directory; an operator's visit imports them.
- **The gear opens Options** (`#/options`): **Dashboards** (each saved one's
  place, read-only switch and home), **Access** (who you are here, what you
  may do, sign in or out), the **Rig file** (the running
  document, versions, save, the runner; the old `#/rig` address lands here),
  **Appearance** (the theme), and **Pages**, a link to every page the bar
  does not reach directly (Readings, Controllers, Graph).

Addresses from before this layout still work: `#/overview` opens the
generated dashboard, `#/inputs` and `#/devices` open Readings, `#/rig`
opens Options › Rig file.

## Pages

| Page | For |
| --- | --- |
| **Dashboards** | saved and generated layouts of widgets — see [Dashboards](../dashboards.md) |
| **Readings** | the plain fall-back view (Options › Pages): every published signal charted, grouped by device or by unit, with each device's commands; `#/readings/<address>` is one signal, `#/devices/<name>` one device. The generated dashboard is the rig at a glance |
| **Controllers** | one card per writable signal: with a controller the card is the faceplate, its device's other signals and commands open inline below; without one, the signal's card alone plus an "Add controller" button. `#/loops` and `#/actuators` redirect here |
| **Programs** | the program library (each row with the rig's check of it: ok, warnings or error; run, delete, upload; **Run a step** runs one step now without saving a program, new) and, for one program, its editor and, while it runs or afterwards, its steps and events. The editor has two tabs over one document: **Steps**, a palette of the rig's step kinds and a card per step whose arguments are a form (drag a chip in as a new step, drag a card's handle to move it; buttons do the same without a pointer; a `command` or `set` step picks a device, then that device's own command or writable signals), and **Text**, the YAML or JSON. Either side updates the other; the rig re-checks the document as it changes and marks the offending step, red for an error and amber for a step naming something the rig lacks right now. Every save is a new version; Save as makes a new program |
| **Events** | the rig's event log, live, filterable by severity; a condition's start and end are marked `raised` and `cleared after …` (how long it held) beside its code; a controller's `interrupted` reads **Put in manual**, its message naming what did it (a command that interrupts, or a stop) |
| **Sessions** | start/stop recording, list recorded sessions and the runner's rolling buffer(s) (if it keeps one) in their own table, keep a range as a session or forget it outright, pin, open a session and rename it, export, delete |
| **Simulation** | simulation-only controls: clock speed, each plant's live parameters, and per-device faults (`fail`, `restore`, `disturb`, `set_limits`) — these never appear on a controller's device section |
| **Options** | behind the gear. **Dashboards** (`#/options/dashboards`, the default tab): order, read-only, home. **Rig file** (`#/options/rig`; `#/rig` redirects here): devices, links and controllers, the running rig as a file would show it and what has changed since the runner started. **Runner** (`#/options/runner`): its version history (the current one marked) and restore, saving it, connecting a model over MCP, and — when the runner allows — restarting or shutting it down. **Access** (`#/options/access`): who this browser is, its verbs, Sign in / Sign out. **Appearance**: the theme. **Pages**: every page not reached from the app bar |

### The playback bar

On a simulated rig, the Simulation page opens with a **Playback** section
once the open session has some history: a video-style transport (rewind,
play/pause, fast-forward, a scrub slider from the session's start to now)
over that session's recorded samples. Absent on a real rig, and on a
simulated one until there is a session with some history to scrub. The
pause reaches every page, not only this one, so on any other page the app
bar's paused chip is the way back to live.

Paused, or scrubbed back, every page shows the rig **as it was at that
moment**: charts end there and show the page's window before it, readouts,
gauges and the health tiles hold the last sample at or before it, a
controller faceplate's reading and trends are from then. The samples come
from the telemetry store, which cuts the window from what it already holds
(up to an hour) or reads it from the session's `/api/history` once a seek
settles — a panel never knows the difference, and nothing moves or changes
size when the page flips between live and history. A signal with no sample
in that window shows a blank value, not a stale or alarm state; a reading
recorded with no value shows `—` and its quality, read off its stored flag; a bool, enum
or JSON signal (a mode, a device's blend) shows what was recorded at that
moment, read from the session.

What is **not** a sample stays live: a controller's mode, target and demand,
a demand's write state, device runs and conditions, program state,
activities and events. The only signs of the paused state are the bar's amber `HH:MM:SS ·
read-only` stamp and its section's faint tint on the Simulation page, and
the app bar's paused chip everywhere else; there is no badge on the panels.
Scrubbing never writes a demand or a setpoint, so it is read-only by
construction;
resuming (the play button, or fast-forwarding past now) goes straight back to
live with no gap, since the live samples kept arriving underneath.

## The app bar

A condition summary sits in the app bar, built from `/api/health` (falling
back to a client-side count from the samples stream on an older runner):

- an always-present **conditions** chip — the count of every condition the
  rig holds at warning or above: a device's faults, and the band alarms it
  raises on signals (`band_warning` or `band_alarm`,
  [Bands](../../2-config/devices/index.md#bands)), coloured by the worst one
  (amber for warn, red for alarm; otherwise the neutral outline every
  healthy state uses — colour is reserved for abnormal conditions), held to
  one width across its own states so it doesn't reflow its neighbours as the
  count changes;
- a **server** chip folding every websocket the page has open into two
  states, not three: green when every stream is open, red otherwise — a
  dropped stream is shown as reconnecting (not yet red) for a few seconds
  before it escalates, so a brief reconnect doesn't read as an outage.
  Hovering names which stream (readings, controllers, activities, events) is
  the problem, when it's known;
- a **recording** dot (a filled circle in a ring, the standard record
  symbol): the open session's name, or "not recording";
- a **program** chip, only while one is running: its name and step;
- a **devices** chip, only while some polled devices are not running: `n/total`;
- a **sim** chip, only when the simulated clock is not at ×1: its speed;
- a **paused** chip, only while playback is paused and the page is not
  Simulation (where the transport itself is): the moment being shown, in
  the playback bar's amber; clicking it goes back to live.

Every chip's tooltip lists the names behind the count (conditions, devices)
and links to the page that explains it (Events, Sessions, Programs, Devices,
Simulation).

Beside the chips, for anyone allowed to operate the rig, is **Software
stop**: after a confirmation it latches the rig, cancels running device
commands, interrupts any running program, puts every controller in manual
and writes each device's stop, for everyone ([the software
stop](../runner/access.md#stopping-the-rig)). The confirmation lists what
the stop will do to each output (`GET /api/rig/stop`): off and its value,
the value the rig file gives, a stop command, or kept, with any warning
beside it. Afterwards a message says how many devices stopped, were left
unchanged or failed (naming each failure), and that the rig is latched;
one with a failure stays until closed, and **Details** opens a table of
what each device wrote and kept. A caller without `operate` does not see
the button at all. A stop that fails says so; it never reports a stop
that did not happen.

While the rig is latched, a banner at the top of every page says who
stopped it, when and why, and that regulation and automatic writes stay
refused. A controller latched by its own fault action (`on_fault`) gets a
banner line of its own. Each line has **Reset**, behind a confirmation
([Reset](../runner/access.md#reset)): it needs `operate`, and it lets the
latch go without writing anything or starting a controller. Controllers
stay in manual until someone sets them regulating.

## Density and theme

**Options › Appearance** sets the theme: follow the system (the default,
from `prefers-color-scheme`), light or dark, kept per browser
(`flyball.theme` in `localStorage`; following the system clears it). It is applied as `data-theme` on `<html>` so the whole app
(MUI and the plain-CSS `packages/react` components alike) reads it from the
same CSS custom properties.

Density is fixed at comfortable (`data-density="comfortable"` on `<html>`,
which sets tile gaps, a tile's title-row height and a readout's minimum
height); there is no compact setting and no toggle.

## Signing in

What the app asks for depends on the door in front of the rig
([Access](../runner/access.md)):

- **the `local` shape** (`flyball run` with nothing configured, or a bare
  runner with no token): nothing; there is no sign-in and no chip;
- **the `password` shape**: a **Sign in** page with one field, the admin
  password;
- **the `proxy` shape**: the proxy's own login, before the app loads. A
  request that reaches the rig without the proxy's sign-in gets a **Sign
  in** page with no field, saying to open the rig through the proxy;
- **a bare runner with a token**: the link the runner printed at start
  signs the browser in once; the **Sign in** page also takes the token
  pasted in.

A wrong password or token is said inline; ten wrong ones in a minute are
refused for the rest of it. A successful sign-in is a cookie the front (or
the bare runner) sets and the browser carries by itself on every request,
socket and download; the app keeps nothing, so there is no secret to find
in its storage or in a copied link, and no token ever goes in the page's
URL. A session ends after 12 idle hours by default, at **Sign out**, or
when the front restarts; the next refusal then brings the page back.

Signed in, a **signed in** chip sits in the app bar beside the other status
chips; its menu has **Sign out**. On a rig anyone may look at
(`anonymous: read`) the app opens without a sign-in and the chip says
**read only**; a control that needs more is refused with a nudge to sign
in, and the chip (or the nudge) leads to the page, which has **Keep
looking** to come back without one. A red **version mismatch** chip means
the dashboard and the rig disagree on the shape of `GET /api/auth`: reload,
or update whichever is older.

**starting…** in place of the page means the front is up and the rig's
runner is not answering yet -- `flyball run` starts the front first. The
app retries on its own, backing off to every 8 s, and opens when the
runner does.

A front that fell back to loopback because of a setting it could not use
puts an amber banner above every page, with its reason; one serving the
`local` shape on the network by `--insecure-open` puts a red one saying
anyone who reaches it may operate the rig. Neither has a close button: it
goes when the setting is fixed.

## Design rationale

Colour is reserved for abnormal states (ISA-101): a normal reading is
neutral, warn and alarm change the tile's border and never only its colour,
and warn and alarm are the rig's word, not the browser's: a tile shows the
band alarm the rig holds on its signal (raised at once, cleared with the
rig's hysteresis), never a check of the value against the bands of its own,
a stale tile (the rig's `stale` reading) is dashed with a hollow status dot,
and a tile whose band is unknown is dotted in its own colour, never red. Every colour, space,
radius and duration is a token in `ui/packages/react/src/styles.css`
(`ui/README.md` *Theming* lists them with their purpose and contrast).
