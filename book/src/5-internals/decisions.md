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
| **D-006** | Device model: quantity (name + unit, not interned), signal (an address and an access set — R/P/W), device (a tree of signals, commands and state), controller (one publishing signal regulated through one writable one) — reverses D-004's measurand/source/channel/loop model, and its process-wide interning of measurands and sources with it; three mutability tiers (declaration frozen, rig-level structure mutable once at startup, per-instant values frozen) | decided — `temp-docs/DEVICE-MODEL-PLAN.md` §1.7–§1.8; not yet transcribed into `DECISIONS.md` at the repository root |

Nothing in this book is settled unless `DECISIONS.md` says so, or — for
D-006, ahead of that transcription — `DEVICE-MODEL-PLAN.md` §1. Where a
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
  still collide — not needed until a second plugin exists
  (`DEVICE-MODEL-PLAN.md` §8.2). Device *names* no longer have this
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
