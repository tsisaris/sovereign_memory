"""A thing that happened, as a named graph node with its time, place and attributes.

Translated unchanged from the removed Cognee file `cognee/modules/engine/models/Event.py`
(upstream commit 1513b1f41^), now pointed at the current Timestamp class. An event holds a
name, a description, one Timestamp or one Interval, a location and the entities that took
part. It never holds more than one time.
"""

from typing import Annotated, Any

from pydantic import SkipValidation

from cognee.infrastructure.engine import DataPoint, Embeddable
from cognee.modules.engine.models.Interval import Interval
from cognee.modules.engine.models.Timestamp import Timestamp


class Event(DataPoint):
    """One event found in a chunk of text, indexed by its name.

    It is translated from the removed Cognee class in `cognee/modules/engine/models/Event.py`.
    It takes a name and, optionally, a description, a Timestamp in `at` or an Interval in
    `during`, a location and `attributes`, which are pairs of an edge and the entities that
    relate to the event by that edge. It gives back a graph node whose name is embedded for
    search (the removed class listed `index_fields: ["name"]` by hand; the `Embeddable` marker
    gives the same list), and it never validates `attributes`, which are attached after
    extraction.
    """

    name: Annotated[str, Embeddable()]
    description: str | None = None
    at: Timestamp | None = None
    during: Interval | None = None
    location: str | None = None
    attributes: SkipValidation[Any] = None
