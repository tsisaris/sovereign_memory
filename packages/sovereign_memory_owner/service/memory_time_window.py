"""Turn the narrowing words of a search request into one filter: a time window and tags.

It is for Mem0's time search, Letta's archival date and tag search and Cognee's time window: one
reader of "since", "until", "when", "reference_date", "tags" and "tag_match", so every kind of
memory is narrowed the same way. It takes the request's fields and the moment the service says is
now; it gives back a RetrievalFilters or None when the request narrows nothing. "when" is a phrase
such as "last week" or "3 days ago" read by dateparser, which says which calendar period the phrase
means (day, week, month or year); the window is that whole period in UTC, weeks starting on Monday.
It never guesses a phrase it cannot read, never narrows by who may see a hit, and never touches a
database.

| Prior-art row | Capability | Tools looked at | Verdict |
| memory_time_window.py | read "last week" or "3 days ago" into a window | dateparser, parsedatetime, python-dateutil, Cognee extract_query_interval (needs a language model), Mem0 reference_date (platform only, closed) | dateparser used as is (already installed with Cognee, its period field gives the whole week or month); the filter wrapper is the only new code |
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from dateparser.date import DateDataParser

from service.memory_owner_core import parse_instant
from service.memory_processor_seam import RetrievalFilters

TAG_MATCHES = ("any", "all")
FILTER_FIELDS = ("since", "until", "when", "reference_date", "tags", "tag_match")


def _period_window(start: datetime, period: str, reference: datetime) -> tuple[datetime, datetime]:
    """Return the calendar period of the given kind that contains start, as (from, before).

    Goes in: a moment, the period dateparser named for the phrase, and the reference moment. Comes
    out: the whole day, Monday-to-Sunday week, month or year in UTC that holds the moment; a phrase
    of a time of day ("2 hours ago") runs from that moment to the reference moment. Raises
    ValueError for a period it does not know.
    """
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return day, day + timedelta(days=1)
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        return monday, monday + timedelta(days=7)
    if period == "month":
        first = day.replace(day=1)
        if first.month == 12:
            following = first.replace(year=first.year + 1, month=1)
        else:
            following = first.replace(month=first.month + 1)
        return first, following
    if period == "year":
        return day.replace(month=1, day=1), day.replace(year=day.year + 1, month=1, day=1)
    if period == "time":
        return start, max(reference, start)
    raise ValueError(f"the time phrase named a period this service does not know: {period!r}")


def window_of_phrase(phrase: str, reference: datetime) -> tuple[datetime, datetime, str]:
    """Read a phrase such as "last week" against a reference moment into (from, before, period).

    Goes in: the phrase and the reference moment (aware). Comes out: the window it means and the
    period's name. Raises ValueError when dateparser cannot read the phrase.
    """
    parser = DateDataParser(
        settings={
            "RELATIVE_BASE": reference.astimezone(timezone.utc).replace(tzinfo=None),
            "PREFER_DATES_FROM": "past",
            "TIMEZONE": "UTC",
            "RETURN_AS_TIMEZONE_AWARE": True,
        }
    )
    data = parser.get_date_data(phrase)
    if data.date_obj is None or data.period is None:
        raise ValueError(f"cannot read the time phrase {phrase!r}")
    start, end = _period_window(data.date_obj.astimezone(timezone.utc), data.period, reference)
    return start, end, data.period


def _moment(request: dict[str, Any], key: str) -> datetime | None:
    """Return the request's field as an aware UTC moment, None when absent; ValueError when not ISO 8601."""
    value = request.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be an ISO 8601 time as text")
    try:
        return parse_instant(value).astimezone(timezone.utc)
    except ValueError as error:
        raise ValueError(f"{key} must be an ISO 8601 time: {error}") from error


def filters_from(request: dict[str, Any], now: datetime) -> RetrievalFilters | None:
    """Read the narrowing fields of a search request; None when it narrows nothing.

    Goes in: the request and now (the moment a phrase is read against when no reference_date is
    given). Comes out: the filter. since and until give the window directly; "when" gives a phrase
    (it may not be combined with since or until) and reference_date moves the moment it is read
    against; tags is a list of non-empty words and tag_match is "any" or "all". Raises ValueError
    naming the field that is wrong, and refuses a window that ends before it begins.
    """
    given = [key for key in FILTER_FIELDS if request.get(key) is not None]
    if not given:
        return None
    since, until = _moment(request, "since"), _moment(request, "until")
    reference = _moment(request, "reference_date")
    phrase = request.get("when")
    period = None
    if phrase is not None:
        if since is not None or until is not None:
            raise ValueError("when cannot be combined with since or until")
        if not isinstance(phrase, str) or not phrase.strip():
            raise ValueError("when must be a phrase such as 'last week'")
        since, until, period = window_of_phrase(phrase, reference or now)
    elif reference is not None:
        raise ValueError("reference_date only means something with a when phrase")
    tags = request.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
        raise ValueError("tags must be a list of non-empty words")
    match = request.get("tag_match", "any")
    if match not in TAG_MATCHES:
        raise ValueError(f"tag_match must be one of {', '.join(TAG_MATCHES)}")
    if since is not None and until is not None and until <= since:
        raise ValueError("until must be after since")
    return RetrievalFilters(since, until, tuple(dict.fromkeys(tags)), match, period)


def hit_moment(hit: dict[str, Any], event_created_at: str | None) -> datetime | None:
    """Return the moment a hit is about, for the time window: its own time if it has one, else its event's.

    Goes in: a hit and the recorded time of its event. Comes out: a Graphiti fact or episode's
    valid_at (the time it holds from), otherwise the time of the event the hit came from: when a Mem0
    memory, a Letta passage or message or a Cognee chunk was said, not when the service stored it.
    None when neither is a readable time. It never invents a moment.
    """
    value = hit.get("valid_at")
    if isinstance(value, str) and value:
        try:
            return parse_instant(value).astimezone(timezone.utc)
        except ValueError:
            pass
    if event_created_at:
        try:
            return parse_instant(event_created_at).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def passes(filters: RetrievalFilters, hit: dict[str, Any], event_created_at: str | None) -> bool:
    """Say whether a hit meets the window and the tags of the filter.

    A hit with no readable moment never meets a window, and a hit with no tags never meets a tag
    filter, so a narrowed search never shows something it could not place.
    """
    if filters.since is not None or filters.until is not None:
        moment = hit_moment(hit, event_created_at)
        if moment is None:
            return False
        if filters.since is not None and moment < filters.since:
            return False
        if filters.until is not None and moment >= filters.until:
            return False
    if filters.tags:
        carried = set(hit.get("tags") or ())
        wanted = set(filters.tags)
        if not carried:
            return False
        if filters.tag_match == "all":
            return wanted <= carried
        return bool(wanted & carried)
    return True
