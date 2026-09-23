# Fault fixtures — these are meant to fail

**Every rig in this directory is deliberately broken.** They exist to give a monitoring
agent, a test, or a person something real to detect — not to demonstrate a working rig.
If you're looking for a rig to copy as a starting point, use `examples/simulated/` or
`examples/scenarios/` instead; nothing here is a template.

Each one produces a fault that stays inside its signals' configured warn/alarm bounds for
a long time (sometimes the whole run) — the point of each fixture is that a naive
per-signal threshold check would see nothing wrong. Built out of this project's existing
`sim_plant`/`sim_daq`/`sim_drive` primitives only — no new engine or driver code, plain
config, same as every other example in `examples/`.

| directory | what's actually wrong | how the fault is injected |
| --- | --- | --- |
| `wiring-mismatch/` | `zone.temperature`'s sensor reads a different, disconnected plant than the one `heater.drive` actually drives — a config/wiring mistake, not a physics fault. The controller winds its integral up chasing a setpoint the sensor can never show it reaching. | pure rig-file config: two separate `sim_plant` links, the DAQ pointed at the wrong one |
| `calibration-mismatch/` | Two "redundant" probes meant to read the same physical process settle at different values (a real, growing disagreement) while each individually stays comfortably inside its own warn band the whole time. | pure rig-file config: two `sim_plant` links given different `ambient`/`gain` values on purpose |

## Origin

Built 18 Sep while testing whether a monitor agent with system context catches faults a
naive threshold check would miss. Promoted out of that experiment's throwaway
`examples/anomaly-test/` directory because these two shapes — "an address pointed at the
wrong device" and "two things that should agree, don't" — are genuinely reusable test
fixtures beyond that one experiment, unlike the other five scenarios tried there, which
were specific to that one test and not kept.

## Running one

```bash
flyball-runner examples/fault-fixtures/wiring-mismatch/rig.yaml
flyball-runner examples/fault-fixtures/wiring-mismatch/rig.yaml \
  examples/fault-fixtures/wiring-mismatch/programs/drive.yaml   # program not required, sets it going
```

`calibration-mismatch/` needs no program — both probes just settle toward their own
(deliberately different) ambient on their own; watch `probe_a.temperature` and
`probe_b.temperature` diverge from the same ~45°C starting point.
