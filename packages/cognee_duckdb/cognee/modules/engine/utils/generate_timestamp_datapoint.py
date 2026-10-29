"""Make a current-flow Timestamp graph node from the time a language model stated for an event.

Translated from the removed Cognee file `cognee/modules/engine/utils/generate_timestamp_datapoint.py`
(upstream commit 1513b1f41^). The removed file built a Timestamp by hand with a node id taken
from the millisecond count and a one-second end. This version writes the same second-precision
string and lets the current `timestamp_from_text` build the node, so an event's time and a date
found by the normal `cognify` for the same instant are one node. `date_to_int` of the removed file
is not kept, because the current Timestamp computes its own `time_at`.
"""

from cognee.modules.engine.models.Timestamp import Timestamp
from cognee.modules.engine.utils.timestamp_from_text import timestamp_from_text
from cognee.tasks.temporal_graph.models import Timestamp as StatedTimestamp


def generate_timestamp_datapoint(ts: StatedTimestamp) -> Timestamp:
    """Return the Timestamp graph node for a stated date and time at second precision.

    It is translated from `generate_timestamp_datapoint` in
    `cognee/modules/engine/utils/generate_timestamp_datapoint.py`. It takes the calendar
    fields the language model stated, written as `YYYY-MM-DD HH:MM:SS`, and gives back the
    current Timestamp node for that second, ending one second later. It never guesses a date:
    a combination that is not a real date (such as 30 February) raises ValueError, as the
    removed file did.
    """
    timestamp_str = (
        f"{ts.year:04d}-{ts.month:02d}-{ts.day:02d} {ts.hour:02d}:{ts.minute:02d}:{ts.second:02d}"
    )
    timestamp = timestamp_from_text(timestamp_str)
    if timestamp is None:
        raise ValueError(f"The stated time {timestamp_str!r} is not a real date")
    return timestamp
