"""The language-model side of the memory service: one router over every part that needs a model.

It is for connecting Graphiti, Cognee, Letta, Mem0 and the Zep summaries to the one
language_model setting, so changing that setting changes every part together. It takes
the validated language_model settings and one object per part, and answers a named step
of a named part, saying which model and which device really answered. It never names a
model in code, never uses the graphics card, and never reports the model it asked for as
if it were the model that answered.
"""

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import httpx
from openai import AsyncOpenAI

OPINIONS = ("main", "second")
DEFAULTED_REQUEST_OPTIONS = ("reasoning_effort", "top_p")


def model_for(language_model: Mapping[str, Any], opinion: str) -> str:
    """Return the model name the settings give for "main" or "second", or raise ValueError.

    It takes the validated language_model settings and the opinion asked for. It returns
    the name exactly as the settings hold it. It never falls back from the second opinion
    to the main model: a missing second_opinion_model is a refusal naming that setting.
    """
    if opinion == "main":
        return language_model["model"]
    if opinion == "second":
        if "second_opinion_model" not in language_model:
            raise ValueError("the setting language_model.second_opinion_model is not set")
        return language_model["second_opinion_model"]
    raise ValueError(f"opinion must be one of {', '.join(OPINIONS)}, not {opinion!r}")


def thinking_off_models(language_model: Mapping[str, Any]) -> frozenset[str]:
    """Return the names of the models the settings say must never think.

    It takes the validated language_model settings and returns every model named by a
    language_model.step_models entry whose think is false. It reads settings only and calls no model.
    """
    return frozenset(
        entry["model"]
        for by_step in language_model.get("step_models", {}).values()
        for entry in by_step.values()
        if entry.get("think") is False
    )


def without_thinking(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return request arguments with thinking switched off the two ways Ollama honours.

    "think": false is what Ollama's own door takes; its OpenAI-style door ignores that field and
    takes reasoning_effort "none" instead (measured 2026-11-02 against Ollama 0.40.9: with think
    false alone the model still thought for 127 s, with reasoning_effort none it answered in
    1.2 s). Both are sent. The caller's other arguments are kept.
    """
    extra = dict(arguments.get("extra_body") or {})
    extra["think"] = False
    return {**arguments, "reasoning_effort": "none", "extra_body": extra}


class AchievedRecorder:
    """Remembers the model name the server reported in its latest answer.

    It is for Law 32: a part reports the value it achieved, not the one it requested. It
    is attached to an HTTP client and sees each finished response. It reads nothing but
    the model field of a JSON answer and never changes a request or an answer.
    """

    def __init__(self) -> None:
        """Start with no answer seen: the achieved model is None and the call count zero."""
        self.last_model: str | None = None
        self.calls = 0

    async def hook(self, response: httpx.Response) -> None:
        """Read one finished response and keep its model field when it has one."""
        await response.aread()
        self.calls += 1
        try:
            body = json.loads(response.content)
        except ValueError:
            return
        if isinstance(body, dict) and isinstance(body.get("model"), str):
            self.last_model = body["model"]


def build_door(language_model: Mapping[str, Any], recorder: AchievedRecorder) -> AsyncOpenAI:
    """Return an OpenAI-style client for the settings' server that records what answered.

    It takes the validated language_model settings and a recorder. It returns a client
    pointed at the server's OpenAI-style door, using the settings' key and time limit.
    It never contacts the server itself and never picks a model: callers name the model
    per request.
    """
    http_client = httpx.AsyncClient(
        timeout=float(language_model["request_timeout_seconds"]),
        event_hooks={"response": [recorder.hook]},
    )
    client = AsyncOpenAI(
        base_url=language_model["base_url"].rstrip("/") + "/v1",
        api_key=language_model["api_key"],
        timeout=float(language_model["request_timeout_seconds"]),
        max_retries=0,
        http_client=http_client,
    )
    defaults = {
        key: value
        for key, value in language_model["options"].items()
        if key in DEFAULTED_REQUEST_OPTIONS
    }
    cap = language_model["options"].get("max_tokens")
    quiet = thinking_off_models(language_model)
    if defaults or cap is not None or quiet:
        original = client.chat.completions.create

        async def create(*args: Any, **kwargs: Any) -> Any:
            """Send a chat request with the settings' request options filled in where the caller gave none.

            The max_tokens setting is a ceiling: a caller that asks for more is cut to it, and a
            caller that names none gets it. A request to a model the settings say must not think
            always carries thinking switched off.
            """
            for key, value in defaults.items():
                kwargs.setdefault(key, value)
            if cap is not None:
                asked = kwargs.get("max_tokens")
                kwargs["max_tokens"] = cap if asked is None else min(asked, cap)
            if kwargs.get("model") in quiet:
                kwargs = without_thinking(kwargs)
            return await original(*args, **kwargs)

        client.chat.completions.create = create
    return client


def _processor_of(language_model: Mapping[str, Any], model: str) -> dict[str, Any]:
    """Ask the server which loaded model sits where; blocking, so callers run it in a thread."""
    address = language_model["base_url"].rstrip("/") + "/api/ps"
    with urllib.request.urlopen(
        urllib.request.Request(address), timeout=float(language_model["request_timeout_seconds"])
    ) as reply:
        loaded = json.loads(reply.read())["models"]
    for entry in loaded:
        if (
            entry.get("name") == model
            or entry.get("model") == model
            or (":" not in model and entry.get("name") == f"{model}:latest")
        ):
            in_card = int(entry.get("size_vram", 0))
            return {
                "loaded": True,
                "size_bytes": int(entry["size"]),
                "size_in_graphics_card_bytes": in_card,
                "processor": "graphics card" if in_card else "processor only",
            }
    return {"loaded": False}


async def processor_of(language_model: Mapping[str, Any], model: str) -> dict[str, Any]:
    """Return where the server really holds a model: processor only, or part in the graphics card.

    It takes the settings and a model name and returns the measurement the server reports
    for that model, or {"loaded": False} when the server has it unloaded. It reads only
    the server's loaded-model list. It never loads, unloads or moves a model.
    """
    try:
        return await asyncio.to_thread(_processor_of, language_model, model)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        return {"loaded": False, "unknown_because": f"{type(error).__name__}: {error}"}


class DatabaseAccess(Protocol):
    """How a part reaches the memory database: short reads and one-transaction writes.

    The service supplies it. A read runs a plain function on the owner's own connection
    handle; a write runs one on a connection of its own inside one transaction that is
    committed when the function returns and rolled back when it raises. A part holds no
    connection itself, and a model call is never made inside either.
    """

    @property
    def path(self) -> Path:
        """The path of the memory database file, for a part that must open a connection of its own."""

    @property
    def numbers(self) -> Any:
        """The service's numbers services (to owe numbers for a derived text), or None when it has none."""

    async def read(self, work: Callable[[Any], Any]) -> Any:
        """Run work(handle) on the owner's handle and return what it returns."""

    async def write(self, work: Callable[[Any], Any]) -> Any:
        """Run work(handle) in one committed transaction and return what it returns."""


class ModelPart(Protocol):
    """What the router needs of each part: its name, its steps and what it achieved."""

    name: str

    def steps(self) -> tuple[str, ...]:
        """Return the names of the steps this part answers."""

    def achieved_model(self) -> str | None:
        """Return the model name the server reported for this part's latest call, or None."""

    async def run_step(self, step: str, args: dict[str, Any], model: str) -> dict[str, Any]:
        """Run one named step with the named model and return a plain dict of the real output."""


class ModelParts:
    """The router: runs a step of a part against the main or the second-opinion model."""

    def __init__(self, language_model: Mapping[str, Any], parts: list[ModelPart]) -> None:
        """Keep the validated settings and the parts by name; refuse two parts of one name."""
        self.language_model = dict(language_model)
        self.parts: dict[str, ModelPart] = {}
        for part in parts:
            if part.name in self.parts:
                raise ValueError(f"two model parts are called {part.name!r}")
            self.parts[part.name] = part

    async def describe(self) -> dict[str, Any]:
        """Report, per part, the models the settings ask for and the model that last answered.

        It takes nothing. It returns the main and second-opinion names from the settings, the
        steps each part answers, the model each part's latest answer came from (None before
        its first call) and where the server holds the main model. It never calls a model.
        """
        main = model_for(self.language_model, "main")
        return {
            "requested": {
                "main": main,
                "second": self.language_model.get("second_opinion_model"),
            },
            "main_model_placement": await processor_of(self.language_model, main),
            "parts": {
                name: {"steps": list(part.steps()), "achieved_model": part.achieved_model()}
                for name, part in self.parts.items()
            },
        }

    def step_model(self, part: str, step: str) -> dict[str, Any] | None:
        """Return the language_model.step_models entry (model, optionally think) of a step, or None."""
        return self.language_model.get("step_models", {}).get(part, {}).get(step)

    def model_of(self, part: str, step: str, opinion: str) -> str:
        """Return the model name a step runs on for an opinion already resolved by opinion_for.

        The opinion "step_model" gives the model the step's step_models setting names; "main" and
        "second" give the settings' main and second-opinion models. It calls no model.
        """
        if opinion == "step_model":
            entry = self.step_model(part, step)
            if entry is None:
                raise ValueError(f"the step {part}.{step} has no language_model.step_models entry")
            return entry["model"]
        return model_for(self.language_model, opinion)

    def opinion_for(self, part: str, step: str, asked: str | None) -> str:
        """Return the opinion a step runs with: the one the settings fix for it, else the one asked, else "main".

        A step listed in language_model.step_models runs on the model that setting names and its opinion
        is "step_model"; a caller that asks for any opinion is refused. A step listed in
        language_model.step_opinions is fixed to that opinion; a caller that asks for the other is
        refused with ValueError, naming the setting. A step in neither takes the caller's opinion,
        "main" when none is given. It calls no model.
        """
        if self.step_model(part, step) is not None:
            if asked is not None:
                raise ValueError(
                    f"the step {part}.{step} runs on the model named by the setting "
                    f"language_model.step_models.{part}.{step}; it cannot run with {asked}"
                )
            return "step_model"
        fixed = self.language_model.get("step_opinions", {}).get(part, {}).get(step)
        if fixed is not None and asked is not None and asked != fixed:
            raise ValueError(
                f"the step {part}.{step} is fixed to the {fixed} model by the setting "
                f"language_model.step_opinions.{part}.{step}; it cannot run with {asked}"
            )
        return fixed or asked or "main"

    async def run(
        self, part: str, step: str, args: dict[str, Any], opinion: str | None
    ) -> dict[str, Any]:
        """Run one step and return its output with the model that answered, the device and the time.

        It takes a part name, a step name, the step's arguments and "main", "second" or None (the
        setting that fixes the step, else "main"). It returns the step's own dict plus the keys part,
        step, opinion (the one really used), requested_model, achieved_model, placement and seconds.
        It raises ValueError for an unknown part, step or opinion, or for an opinion the settings
        forbid for that step, and never retries a failed step.
        """
        if part not in self.parts:
            raise ValueError(f"unknown part {part!r}; the parts are {', '.join(self.parts)}")
        target = self.parts[part]
        if step not in target.steps():
            raise ValueError(
                f"part {part!r} has no step {step!r}; its steps are {', '.join(target.steps())}"
            )
        opinion = self.opinion_for(part, step, opinion)
        model = self.model_of(part, step, opinion)
        started = time.monotonic()
        output = await target.run_step(step, args, model)
        seconds = round(time.monotonic() - started, 2)
        return {
            **output,
            "part": part,
            "step": step,
            "opinion": opinion,
            "requested_model": model,
            "achieved_model": target.achieved_model(),
            "placement": await processor_of(self.language_model, model),
            "seconds": seconds,
        }
