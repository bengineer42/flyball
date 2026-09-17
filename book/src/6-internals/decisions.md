# Decisions

Options weighed and chosen. The full records, with context and consequences,
are in `DECISIONS.md` at the repository root; this is the index.

| | decision | status |
| --- | --- | --- |
| **D-001** | Control loop execution model: what sets the cadence, whether stages run in lockstep, what concurrency carries them | open; needs measurement of the plant and the link |
| **D-002** | Serialisation formats: YAML documented for both programs and rig files (deep envelopes read better, and programs are already YAML); TOML still accepted for a rig file in the same shape; JSON on the wire | decided |
| **D-003** | Addressing controllers: which controller a step means is a field with a program-level default — the Python program-step dataclasses still call the field `loop`, though the concept and the class are now `Controller`; coupled segments are two steps that start together | decided, with an escape hatch recorded |
| **D-004** | ~~Readings model: measurand, source, channel, reading, sample; units on the measurand only~~ | superseded by D-006 |
| **D-005** | Front-end framework: React + TypeScript with Vite; `react-jsonschema-form`, uPlot, `react-grid-layout` | decided |
| **D-006** | Device model: quantity (name + unit, not interned), signal (an address, a role and an access set — R/P/W), device (a static tree of signals and commands over one store), controller (one publishing signal regulated through one demand); demands are what controllers drive, commands are what people and programs run, outputs are read — reverses D-004's measurand/source/channel/loop model and its process-wide interning | decided |
| **D-007** | Daemon config and a public daemon: a `daemon:` section in the rig file (or a file that `extends` it) with the process settings, flags overriding; `--root-path` served by the daemon itself and a UI that finds its API from where it was served; writes behind `--allow-save` / `--allow-shutdown` / `--no-mcp`, all off by default; rig versions as a tree with a head, a restore moving the head rather than copying | decided |
| **D-008** | The scratch record and retention: a rig nobody is recording still keeps its last hour, in the store (not memory), as a scratch session written *instead of* a recording rather than beside one; trimming moves the session's start against a hidden origin instead of rewriting offsets; a per-session size estimate plus the real file size for the cap; `keep` / `keep_size` / `retain` / `rotate` / `max_store` in the `daemon:` section, pinned sessions exempt, the oldest data first under the cap whatever its kind | decided |
| **D-010** | Authentication: a password for people, traded at the UI's login page for a signed `HttpOnly` cookie (no session table; the key beside the store; a changed password signs everyone out), the bearer token kept for machines, `scrypt` in the file, `anonymous: none | read` for a rig the public may watch; one principal with a level per request and one comparison, so several sign-ins or a locked part of the rig later change who gets a level, not the routes | decided |

Nothing in this book is settled unless `DECISIONS.md` says so. Where a
chapter describes intent rather than fact, it says which.

## Open questions

- **Limits and interlocks.** A writable signal now carries `limits`,
  clamped on every demand (D-006) — that closes the "no setpoint bounds"
  half of this. Still open: no rate-of-change clamp, no runaway detection,
  no failsafe on a stale sensor.
- **Adaptation in control.** Estimator and retune policy exist; wiring them
  into a controller is not done.
- **Per-driver registries.** Driver tags (`flyball.core.config.Config.registry`)
  are one process-wide namespace, so two plugins declaring the same tag
  still collide — not needed until a second plugin exists. Device *names* no longer have this
  problem: they are claimed per rig (`Rig.claim`), not process-wide, which
  is part of what D-006 fixed relative to D-004's interned sources and
  measurands.
- **Model-based control.** MPC is the natural home for limits and would take
  the identified model directly. Not started.

Resolved since the last pass, and dropped from this list: **multi-loop** —
a controller is addressable, carries its own source and mode, and the rig
now hosts as many as have targets (`Controllers`, keyed by the target's
address, with a `default`). **Events** — `core.device.Event`, `rig.events`
and `rig.recent` exist; the programmer records step outcomes through
`rig.event(...)`; a session records them (`SessionWriter.write_event`).
