"""Run a simulated rig fast-forward and print what the loop does.

    uv run python demo.py oven.toml 50 600      # aim at 50 °C, watch 600 s
    uv run python demo.py tank.toml 40 120

No daemon, no threads: the rig's clock is stepped, so a ten-minute run takes
a moment. Everything the daemon would do -- tick, apply, record -- happens
the same way, which is the point of `flyball.sim`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from flyball.db.sqlite import SqliteStore
from flyball.runtime.config import load_rig_config
from flyball.sim import SteppedClock


def main(path: str, setpoint: float, seconds: float) -> None:
    config = load_rig_config(Path(path))
    clock = SteppedClock(0)
    rig = config.build(clock=clock, start=False)  # we drive the reads ourselves
    (reader,) = rig.readers.by_name.values()
    (loop_name,) = rig.loops
    loop = rig.loops[loop_name]
    period = config.readers[0].period_s or 1.0

    store = SqliteStore(":memory:")
    rig.start_recording(store)
    rig.read(reader)
    loop.regulate(setpoint)

    steps = int(seconds / period)
    print(
        f"{config.name}: regulating {loop.name} to {setpoint} for {seconds:.0f} s at {period} s per tick"
    )
    for i in range(steps):
        clock.advance(period)
        rig.read(reader)
        if i % max(1, steps // 12) == 0 or i == steps - 1:
            state = loop.state
            reading = state.reading.value if state.reading else float("nan")
            t = clock.from_start_s(clock.now_ns())
            actuator = rig.actuators[loop.name]
            print(
                f"  t={t:6.0f}s  reading={reading:8.3f}  demand={state.demand:8.3f}"
                f"  correction={state.correction:+8.3f}  input={actuator.state.input:6.3f}"
            )
    rig.stop_recording()
    session = store.sessions()[0]
    print(f"recorded session {session.id}: {len(store.ticks(session.id, loop.name))} ticks")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else 600.0)
