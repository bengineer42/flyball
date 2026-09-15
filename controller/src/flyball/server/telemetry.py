"""The rig's live feed, for the websockets.

The server attaches one observer that turns every sample into a wire model
and fans it out through a [Topic][flyball.core.topic.Topic]. With nobody
connected the topic drops it, so an idle server costs the control thread
nothing.
"""

from __future__ import annotations

from flyball.core.reading import Reading, Sample
from flyball.core.sink import Observer
from flyball.core.topic import Topic
from flyball.runtime.rig import Rig

from .schemas import SampleOut


class Telemetry(Observer):
    """Attach to a rig; `samples` then carries everything the rig hears."""

    def __init__(self, rig: Rig) -> None:
        self.samples: Topic[SampleOut] = Topic()
        self.observes = frozenset(rig.sources)

    def observe(self, sample: Sample | Reading) -> None:
        if isinstance(sample, Sample) and self.samples.subscribed:
            self.samples.publish(SampleOut.of(sample))
