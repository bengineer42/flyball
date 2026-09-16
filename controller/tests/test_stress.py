"""The stress rigs in `examples/stress/`.

They load, build, and -- where they have anything to read or regulate --
every declared channel samples and every loop that is told to regulate gets
a demand. Not a settling test: several of these are deliberately badly
behaved (`chaos.toml`); the point is that the rig runs at all under the
load, not that it controls well.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flyball.core.reading import Source
from flyball.runtime.config import RigConfig, load_rig_config, resolve_document

STRESS = Path(__file__).resolve().parents[2] / "examples" / "stress"
RIGS = sorted(STRESS.glob("*.toml"))


def _reader_names(config: RigConfig) -> list[str]:
    return [entry.device.name for entry in config.readers]


@pytest.fixture
def built(request):
    """`(config, rig)` for one stress file, stepped, sources forgotten again on teardown."""
    path = request.param
    document, _ = resolve_document(path)
    document["clock"] = {"stepped": True}
    config = RigConfig.model_validate(document)
    rig = config.build()
    try:
        yield config, rig
    finally:
        rig.stop()
        for name in _reader_names(config):
            Source.forget(name)


def test_every_stress_rig_validates():
    for path in RIGS:
        config = load_rig_config(path)
        assert config.name, path.name


@pytest.mark.parametrize("built", RIGS, indirect=True, ids=[p.name for p in RIGS])
def test_stress_rig_samples_every_channel_and_regulates_every_loop(built):
    config, rig = built
    clock = rig.clock
    periods = [entry.period_s for entry in config.readers if entry.period_s]
    period = max(periods) if periods else 1.0

    # Enough steps for the slowest reader to have polled at least a couple of times.
    clock.advance(period * 3 + 1)

    missing = [
        channel.name
        for reader in rig.readers.by_name.values()
        for source in reader.sources
        for channel in source.channels
        if rig.reading(channel) is None
    ]
    assert not missing, f"no sample yet on {missing}"

    # Aim every loop at roughly where it already is, then give it a couple more
    # readings to act on: a demand should come out the other side regardless of
    # the law -- P, PI, PID, open_loop -- or how badly it is tuned.
    for name, loop in list(rig.loops.items()):
        reading = rig.reading(rig.loops.channel(name))
        assert reading is not None, f"loop {name} has no reading to regulate from"
        loop.regulate(reading.value)
    clock.advance(period * 2 + 1)

    undemanded = [name for name in rig.loops if rig.actuators[name].state.demand is None]
    assert not undemanded, f"no demand yet on {undemanded}"
