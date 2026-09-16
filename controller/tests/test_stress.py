"""The stress rigs in `examples/stress/`.

They load, build, and -- where they have anything to read or regulate --
every publishing signal samples and every controller that is told to
regulate gets a demand out. Not a settling test: several of these are
deliberately badly behaved (`chaos.yaml`); the point is that the rig runs at
all under the load, not that it controls well.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball.core.signal import Access
from flyball.runtime.config import RigConfig, load_rig_config, resolve_document

STRESS = Path(__file__).resolve().parents[2] / "examples" / "stress"
RIGS = sorted(STRESS.glob("*.yaml"))


@pytest.fixture
def built(request):
    """`(config, rig)` for one stress file, stepped, stopped again on teardown."""
    path = request.param
    document, _ = resolve_document(path)
    document["clock"] = {"stepped": True}
    config = RigConfig.model_validate(document)
    rig = config.build()
    try:
        yield config, rig
    finally:
        rig.stop()


def test_every_stress_rig_validates():
    assert [p.name for p in RIGS] == [
        "bare.yaml",
        "chaos.yaml",
        "longrun.yaml",
        "plant.yaml",
        "sparse.yaml",
        "torrent.yaml",
        "zoo.yaml",
    ]
    for path in RIGS:
        config = load_rig_config(path)
        assert config.name == path.stem, path.name


@pytest.mark.parametrize("built", RIGS, indirect=True, ids=[p.name for p in RIGS])
def test_stress_rig_samples_every_signal_and_regulates_every_controller(built):
    config, rig = built
    clock = rig.clock
    publishing = [
        signal
        for device in rig.devices.values()
        for signal in device.signals.values()
        if Access.P in signal.access
    ]
    periods = [s.poll_s for s in publishing if s.poll_s is not None]
    period = max(periods) if periods else 1.0

    # Enough steps for the slowest signal to have been read at least a couple of times.
    clock.advance(period * 3 + 1)

    missing = [s.address for s in publishing if s not in rig.latest]
    assert not missing, f"no reading yet on {missing}"

    # Aim every controller at roughly where it already is, then give it a couple
    # more readings to act on: a demand should come out the other side regardless
    # of the law -- P, PI, PID, open_loop -- or how badly it is tuned.
    for _, controller in list(rig.controllers.items()):
        reading = rig.latest.get(controller.source)
        assert reading is not None, f"controller {controller.name} has no reading to regulate from"
        controller.regulate(reading.value)
    clock.advance(period * 2 + 1)

    undemanded = [
        name
        for name, controller in rig.controllers.items()
        if controller.target not in controller.target.device.written
    ]
    assert not undemanded, f"no demand yet on {undemanded}"
    assert all(c.state.demand is not None for _, c in rig.controllers.items())
