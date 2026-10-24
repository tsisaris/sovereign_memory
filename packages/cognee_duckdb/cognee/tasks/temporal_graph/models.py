"""The shapes the language model fills in when it finds events and the entities around them.

Translated from the removed Cognee file `cognee/tasks/temporal_graph/models.py` (upstream
commit 1513b1f41^). `Interval` and `QueryInterval` of that file are left out: nothing in the
event tasks used them, and the question-to-window model now lives in
`cognee/modules/retrieval/temporal_hybrid/models.py`. These classes only describe an answer
and never touch the graph.
"""

from pydantic import BaseModel, Field


class Timestamp(BaseModel):
    """A date and time as the language model states it, year required and the rest defaulted.

    It is translated from `Timestamp` in `cognee/tasks/temporal_graph/models.py`. It takes the
    calendar fields the model found and fills each unknown part with its earliest value. It
    gives back that checked record and never makes a graph node; the graph node is made later
    by `generate_timestamp_datapoint`.
    """

    year: int = Field(
        ...,
        ge=1,
        le=9999,
        description="Always required. If only a year is known, use it.",
    )
    month: int = Field(1, ge=1, le=12, description="If unknown, default to 1")
    day: int = Field(1, ge=1, le=31, description="If unknown, default to 1")
    hour: int = Field(0, ge=0, le=23, description="If unknown, default to 0")
    minute: int = Field(0, ge=0, le=59, description="If unknown, default to 0")
    second: int = Field(0, ge=0, le=59, description="If unknown, default to 0")


class Event(BaseModel):
    """One event the language model found in a text.

    It is translated from `Event` in `cognee/tasks/temporal_graph/models.py`. It takes a
    name, an optional description, an optional start and end time and an optional location.
    It gives back that checked record and never decides whether the event is real.
    """

    name: str
    description: str | None = None
    time_from: Timestamp | None = None
    time_to: Timestamp | None = None
    location: str | None = None


class EventList(BaseModel):
    """All the events the language model found in one chunk of text.

    It is translated from `EventList` in `cognee/tasks/temporal_graph/models.py`. It takes a
    list of events and gives back that list unchanged. It never merges or drops events.
    """

    events: list[Event]


class EntityAttribute(BaseModel):
    """One entity that takes part in an event, with its type and how it relates to the event.

    It is translated from `EntityAttribute` in `cognee/tasks/temporal_graph/models.py`. It
    takes the entity name, its type and a short relationship word. It gives back that checked
    record and never creates a graph node.
    """

    entity: str
    entity_type: str
    relationship: str


class EventWithEntities(BaseModel):
    """An event name and description together with the entities found around it.

    It is translated from `EventWithEntities` in `cognee/tasks/temporal_graph/models.py`.
    It takes the event name, an optional description and a list of entity attributes. It gives
    back that checked record and never looks the event up in the graph.
    """

    event_name: str
    description: str | None = None
    attributes: list[EntityAttribute] = []


class EventEntityList(BaseModel):
    """The entities found around every event of one request, in the order the events were sent.

    It is translated from `EventEntityList` in `cognee/tasks/temporal_graph/models.py`. It
    takes one record per event and gives back the list unchanged. It never reorders the
    records, because the caller pairs them with its events by position.
    """

    events: list[EventWithEntities]
