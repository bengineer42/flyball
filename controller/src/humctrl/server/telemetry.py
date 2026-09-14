"""The rig's live feed, for the websockets.

The rig publishes nothing itself; the server attaches one observer that turns
every sample into a wire model and fans it out through a :class:`Topic`. When
nobody is connected the topic drops it in ~25 ns, so an idle server costs the
control thread nothing.
"""

from __future__ import annotations

from humctrl.core.reading import Reading, Sample
from humctrl.core.sink import Observer
from humctrl.core.topic import Topic
from humctrl.runtime.rig import Rig

from .schemas import SampleOut


class Telemetry(Observer):
    """Attach to a rig; ``samples`` then carries everything the rig hears."""

    def __init__(self, rig: Rig) -> None:
        self.samples: Topic[SampleOut] = Topic()
        self.observes = frozenset(rig.sources)

    def observe(self, sample: Sample | Reading) -> None:
        if isinstance(sample, Sample) and self.samples.subscribed:
            self.samples.publish(SampleOut.of(sample))
