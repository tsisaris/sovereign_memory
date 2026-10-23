"""Make an Event graph node from an event the language model found.

Translated unchanged in behaviour from the removed Cognee file
`cognee/modules/engine/utils/generate_event_datapoint.py` (upstream commit 1513b1f41^). Its
times are made by `generate_timestamp_datapoint`, so they are current-flow Timestamp nodes.
It adds the time to the event's description as the removed file did.
"""

from cognee.modules.engine.models.Event import Event
from cognee.modules.engine.models.Interval import Interval
from cognee.modules.engine.utils.generate_timestamp_datapoint import generate_timestamp_datapoint


def generate_event_datapoint(event) -> Event:
    """Return an Event node from a found event, with its time attached.

    It is translated from `generate_event_datapoint` in
    `cognee/modules/engine/utils/generate_event_datapoint.py`. It takes a found event with a
    name, description, location, time_from and time_to; with both times it makes an Interval,
    with one it makes a Timestamp, and in either case it appends the time to the description
    after a `---` line. It never invents a time for an event that stated none.
    """
    event_data = {
        "name": event.name,
        "description": event.description,
        "location": event.location,
    }

    time_from = generate_timestamp_datapoint(event.time_from) if event.time_from else None
    time_to = generate_timestamp_datapoint(event.time_to) if event.time_to else None

    if time_from and time_to:
        event_data["during"] = Interval(time_from=time_from, time_to=time_to)
        temporal_info = f"\n---\nTime data: {time_from.timestamp_str} to {time_to.timestamp_str}"
        event_data["description"] = (event_data["description"] or "Event") + temporal_info
    elif time_from or time_to:
        timestamp = time_from or time_to
        event_data["at"] = timestamp
        temporal_info = f"\n---\nTime data: {timestamp.timestamp_str}"
        event_data["description"] = (event_data["description"] or "Event") + temporal_info

    return Event(**event_data)
