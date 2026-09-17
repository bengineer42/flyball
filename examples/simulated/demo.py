"""Run a simulated rig fast-forward and print what the controller does.

    uv run python demo.py oven.yaml 50 600      # aim at 50 °C, watch 600 s
    uv run python demo.py tank.yaml 40 120

No runner, no threads: the rig's clock is stepped, so a ten-minute run takes
a moment. Everything the runner would do -- tick, apply, record -- happens
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
    ((_, controller),) = rig.controllers.items()  # the example's one controller
    source = controller.source
    period = source.poll_s or 1.0

    store = SqliteStore(":memory:")
    rig.start_recording(store)
    rig.read(source, fresh=True)
    controller.regulate(setpoint)

    steps = int(seconds / period)
    print(
        f"{config.name}: regulating {controller.name} to {setpoint} for {seconds:.0f} s"
        f" at {period} s per tick"
    )
    for i in range(steps):
        clock.advance(period)
        rig.read(source, fresh=True)
        if i % max(1, steps // 12) == 0 or i == steps - 1:
            state = controller.state
            reading = state.reading.value if state.reading else float("nan")
            t = clock.from_start_s(clock.now_ns())
            written = controller.target.device.written.get(controller.target)
            value = written.value if written is not None and written.value is not None else 0.0
            print(
                f"  t={t:6.0f}s  reading={reading:8.3f}  demand={state.demand:8.3f}"
                f"  correction={state.correction:+8.3f}  {controller.target.address}={value:8.3f}"
            )
    rig.stop_recording()
    session = store.sessions()[0]
    print(f"recorded session {session.id}: {len(store.ticks(session.id, controller.name))} ticks")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else 600.0)
