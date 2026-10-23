"""A period with a stated start and a stated end, held by an event.

Translated unchanged from the removed Cognee file `cognee/modules/engine/models/Interval.py`
(upstream commit 1513b1f41^). It carries the two current-flow Timestamp nodes an event
starts and ends at. It takes both bounds and never fills in a missing one.
"""

from pydantic import Field

from cognee.infrastructure.engine import DataPoint
from cognee.modules.engine.models.Timestamp import Timestamp


class Interval(DataPoint):
    """The period an event lasted, as a start Timestamp and an end Timestamp.

    It is translated from the removed Cognee class of the same name in
    `cognee/modules/engine/models/Interval.py`. It takes two Timestamp nodes, both
    required, and gives back a graph node with an edge to each. It never accepts an
    interval with only one end.
    """

    time_from: Timestamp = Field(...)
    time_to: Timestamp = Field(...)
