"""Warn and alarm bands: declared on a measurand, set from a rig file, published on a channel."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from flyball.core.reading import Measurand, Source
from flyball.core.units.si import Celsius
from flyball.runtime.config import load_rig_config
from flyball.server import create_app, set_rig
from flyball.server.schemas import ChannelOut

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "simulated"


def test_measurand_carries_bands_and_defaults_to_none(fresh):
    plain = Measurand(fresh("temperature"), Celsius, range=(-40.0, 125.0), precision=2)
    assert plain.warn is None and plain.alarm is None
    banded = Measurand(fresh("temperature"), Celsius, warn=(30.0, 90.0), alarm=(10.0, 110.0))
    assert banded.warn == (30.0, 90.0) and banded.alarm == (10.0, 110.0)


def test_channel_out_serialises_bands(fresh):
    measurand = Measurand(fresh("temperature"), Celsius, warn=(30.0, 90.0), alarm=(10.0, 110.0))
    source = Source(fresh("probe"), (measurand,))
    out = ChannelOut.of(source[measurand]).model_dump()
    assert out["warn"] == (30.0, 90.0) and out["alarm"] == (10.0, 110.0)
    plain = Measurand(fresh("temperature"), Celsius)
    assert ChannelOut.of(Source(fresh("probe"), (plain,))[plain]).model_dump()["warn"] is None


def test_oven_rig_file_sets_bands_on_the_thermocouple():
    Measurand.forget("temperature")  # another test may have interned it without bands
    Source.forget("thermocouple")
    rig = load_rig_config(EXAMPLES / "oven.toml").build(start=False)
    set_rig(rig)
    try:
        with TestClient(create_app()) as client:
            (channel,) = client.get("/api/sources/thermocouple").json()["channels"]
    finally:
        set_rig(None)
        Source.forget("thermocouple")
    assert channel["range"] == [0.0, 120.0] and channel["precision"] == 2
    assert channel["warn"] == [30.0, 90.0] and channel["alarm"] == [10.0, 110.0]
