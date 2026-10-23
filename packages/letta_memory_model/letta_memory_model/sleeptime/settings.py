"""The sleep-time and dreaming settings, for the numbers Letta wrote into its code.

They are for how long a background memory agent may work and when dreaming starts.
What goes in is a plain dict read from the memory database's settings; what comes
out is a checked object. Every field is required and none has a default: the
original's values (DEFAULT_MAX_STEPS 50 in letta/constants.py, the dreaming step
count in Letta Code's memory-reminder.ts) are published but this project keeps every
tunable number in its settings. They never read the database themselves.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SleeptimeSettings(BaseModel):
    """How a sleep-time agent run is bounded: its most model steps.

    Translated from DEFAULT_MAX_STEPS in letta/constants.py, which the sleep-time
    agent's step() takes as max_steps. The field is required and has no default.
    It never reads storage.
    """

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(description="Most model calls one sleep-time run may make.")


class DreamSettings(BaseModel):
    """When dreaming starts and how long a dream may run.

    Translated from ReflectionSettings in Letta Code's src/cli/helpers/memory-reminder.ts
    (trigger and stepCount) and the same max_steps bound as sleep-time. trigger is off,
    step-count (a dream after stepCount new assistant steps) or compaction-event (a dream
    after a compaction). All fields are required. It never reads storage.
    """

    model_config = ConfigDict(extra="forbid")

    trigger: Literal["off", "step-count", "compaction-event"] = Field(
        description="What starts a dream."
    )
    step_count: int = Field(
        description="New assistant steps that start a dream when trigger is step-count."
    )
    max_steps: int = Field(description="Most model calls one dream may make.")


def parse_sleeptime_settings(raw: dict[str, Any]) -> SleeptimeSettings:
    """Check a settings dict and return it as SleeptimeSettings.

    Goes in: the dict from the database; comes out: the checked object. A missing or
    unknown key, a wrong type or a max_steps below 1 raises ValueError. It never fills in a value.
    """
    settings = SleeptimeSettings.model_validate(raw)
    if settings.max_steps < 1:
        raise ValueError("sleeptime.max_steps must be at least 1")
    return settings


def parse_dream_settings(raw: dict[str, Any]) -> DreamSettings:
    """Check a settings dict and return it as DreamSettings.

    Goes in: the dict from the database; comes out: the checked object. A missing or
    unknown key, a wrong type, a step_count or max_steps below 1 raises ValueError. It
    never fills in a value.
    """
    settings = DreamSettings.model_validate(raw)
    if settings.step_count < 1 or settings.max_steps < 1:
        raise ValueError("dream.step_count and dream.max_steps must be at least 1")
    return settings
