# Decisions

Options weighed and chosen. The full records, with context and consequences,
are in `DECISIONS.md` at the repository root; this is the index.

| | decision | status |
| --- | --- | --- |
| **D-001** | Control loop execution model: what sets the cadence, whether stages run in lockstep, what concurrency carries them | open; needs measurement of the plant and the link |
| **D-002** | Serialisation formats: YAML for programs, TOML for rig configuration, JSON on the wire | decided |
| **D-003** | Addressing control loops: which loop a step means is a field with a program-level default; coupled segments are two steps that start together | decided, with an escape hatch recorded |
| **D-004** | Readings model: measurand, source, channel, reading, sample; units on the measurand only | decided |
| **D-005** | Front-end framework: React + TypeScript with Vite; `react-jsonschema-form`, uPlot, `react-grid-layout` | decided |

Nothing in this book is settled unless `DECISIONS.md` says so. Where a
chapter describes intent rather than fact, it says which.

## Open questions

- **Multi-loop.** The pieces are in place — a loop is addressable and carries
  its own channel and mode — but the rig hosts one today.
- **Limits and interlocks.** No setpoint bounds, no rate-of-change clamp, no
  runaway detection, no failsafe on a stale sensor. For an instrument used
  by non-specialists this is the largest gap.
- **Adaptation in the loop.** Estimator and retune policy exist; wiring
  them in is not done.
- **Events.** Conditions (what is true now) live in device state. The
  companion stream — what *happened* — is designed but not built; the
  programmer's warnings are its first customer, and the program route waits
  on it.
- **Per-rig registries.** Sources and measurands register process-wide,
  which rules out two rigs in one process.
- **Model-based control.** MPC is the natural home for limits and would take
  the identified model directly. Not started.
