"""Memory's four tunable search settings, read at run time and recorded as parameter records.

The settings are the score used to compare combiners, the held-back share, the
smallest held-back set that may decide, and the merging constant. They are never
written into code (project Law 32): the deployment's configuration file declares
them, the service validates them at start so a missing or invalid value fails
clearly, and every value in effect is appended to the memory_settings table, so
an answer or a comparison can report the value it used. This module only holds,
validates and records them; it chooses no value, ranks nothing and calls no model.
"""

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import duckdb

SETTING_NAMES = (
    "comparison_score",
    "held_back_share",
    "smallest_held_back_set",
    "merging_constant",
)

# Optional: absent means the feature is off. decay_time_constant_seconds turns on the
# recency bias on search scores; no source publishes a value, so there is no default.
OPTIONAL_SETTING_NAMES = (
    "decay_time_constant_seconds",
    "decay_lower_bound",
    "decay_upper_bound",
    "words_capability",
)

SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_settings (
    name VARCHAR NOT NULL,
    value VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    recorded_at VARCHAR NOT NULL
);
"""


@dataclass(frozen=True)
class SearchSettings:
    """The four declared settings, exactly as the deployment gave them.

    comparison_score names one of the scores memory can compute; held_back_share
    is the fraction of cases held back, strictly between 0 and 1;
    smallest_held_back_set is the fewest held-back cases that may decide a
    comparison; merging_constant is the constant added to a rank in reciprocal
    rank fusion. None has a default. decay_time_constant_seconds is optional: when
    the deployment declares it, search scores are biased by the age of their event;
    when it does not, search ordering is exactly what the combiner gave. The decay
    bias is held between decay_lower_bound and decay_upper_bound, so the three decay
    settings are declared together or not at all.
    """

    comparison_score: str
    held_back_share: float
    smallest_held_back_set: int
    merging_constant: float
    decay_time_constant_seconds: float | None = None
    decay_lower_bound: float | None = None
    decay_upper_bound: float | None = None
    words_capability: str | None = None

    @classmethod
    def from_mapping(cls, table: Any, known_scores: tuple[str, ...]) -> "SearchSettings":
        """Validate a declared table and return the settings; nothing is defaulted.

        Goes in: a mapping with exactly the four setting names, and the score
        names memory can compute. Comes out: the settings. Raises ValueError
        naming the setting that is missing, unknown, of the wrong type or out
        of range.
        """
        if not isinstance(table, Mapping):
            raise ValueError("The search settings must be a table")
        missing = [name for name in SETTING_NAMES if name not in table]
        if missing:
            raise ValueError(f"The search settings are missing: {', '.join(missing)}")
        unknown = sorted(set(table) - set(SETTING_NAMES) - set(OPTIONAL_SETTING_NAMES))
        if unknown:
            raise ValueError(f"The search settings have unknown keys: {', '.join(unknown)}")
        score = table["comparison_score"]
        if score not in known_scores:
            raise ValueError(f"comparison_score must be one of {list(known_scores)}")
        share = table["held_back_share"]
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share < 1:
            raise ValueError("held_back_share must be a number strictly between 0 and 1")
        smallest = table["smallest_held_back_set"]
        if isinstance(smallest, bool) or not isinstance(smallest, int) or smallest < 1:
            raise ValueError("smallest_held_back_set must be a positive integer")
        constant = table["merging_constant"]
        if isinstance(constant, bool) or not isinstance(constant, (int, float)):
            raise ValueError("merging_constant must be a number")
        if not math.isfinite(constant) or constant <= 0:
            raise ValueError("merging_constant must be a finite number above 0")
        decay = table.get("decay_time_constant_seconds")
        if decay is not None and (
            isinstance(decay, bool)
            or not isinstance(decay, (int, float))
            or not math.isfinite(decay)
            or decay <= 0
        ):
            raise ValueError("decay_time_constant_seconds must be a finite number above 0")
        lower = table.get("decay_lower_bound")
        upper = table.get("decay_upper_bound")
        declared = [value is not None for value in (decay, lower, upper)]
        if any(declared) and not all(declared):
            raise ValueError(
                "decay_time_constant_seconds, decay_lower_bound and decay_upper_bound "
                "must be declared together"
            )
        for name, bound in (("decay_lower_bound", lower), ("decay_upper_bound", upper)):
            if bound is not None and (
                isinstance(bound, bool)
                or not isinstance(bound, (int, float))
                or not math.isfinite(bound)
                or bound < 0
            ):
                raise ValueError(f"{name} must be a finite number, zero or above")
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("decay_lower_bound must not be above decay_upper_bound")
        words = table.get("words_capability")
        if words is not None and (not isinstance(words, str) or not words.strip()):
            raise ValueError("words_capability must be the supplier's capability id, as text")
        return cls(
            score,
            float(share),
            smallest,
            float(constant),
            None if decay is None else float(decay),
            None if lower is None else float(lower),
            None if upper is None else float(upper),
            words,
        )

    def report(self) -> dict[str, Any]:
        """Return the values as a plain dict, for reporting the value achieved with an answer."""
        return asdict(self)


def initialize_settings_schema(handle: duckdb.DuckDBPyConnection) -> None:
    """Create the settings table if absent, inside the caller's transaction."""
    handle.execute(SETTINGS_SCHEMA)


def record_settings(
    handle: duckdb.DuckDBPyConnection, settings: SearchSettings, source: str, recorded_at: str
) -> list[str]:
    """Append every setting whose value differs from its latest record; return the names written.

    The table is append-only: a changed setting adds a row, an unchanged one
    adds nothing, and no earlier row is edited or removed.
    """
    written = []
    for name, value in settings.report().items():
        encoded = json.dumps(value)
        latest = handle.execute(
            "SELECT value FROM memory_settings WHERE name = ? ORDER BY recorded_at DESC, "
            "rowid DESC LIMIT 1",
            [name],
        ).fetchone()
        if latest is None or latest[0] != encoded:
            handle.execute(
                "INSERT INTO memory_settings VALUES (?, ?, ?, ?)",
                [name, encoded, source, recorded_at],
            )
            written.append(name)
    return written
