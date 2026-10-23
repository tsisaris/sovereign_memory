"""Turn the language_model settings into the clients and environment the memory engines need.

The model's name, the address it answers on, its key, its time limit and its options
all come from the settings in the memory database (config.RuntimeConfig.language_model);
nothing here names a model. graphiti_client gives Graphiti an OpenAI-style client for
the local server, and cognee_environment gives Cognee the environment variables its own
settings read. Both take the settings dict as validated by config and return what the
engine needs. They never read the database, never start a server and never call the model.
"""

from types import SimpleNamespace
from typing import Any

from .config import ConfigurationError

REQUEST_OPTION_KEYS_NOT_SENT = ("temperature",)


class _OptionedCompletions:
    """Sends every chat request with the configured options added to it."""

    def __init__(self, completions: Any, options: dict[str, Any]) -> None:
        """Keep the real completions object and the options to add."""
        self._completions = completions
        self._options = options

    async def create(self, **request: Any) -> Any:
        """Send the request with the options added; a value in the request itself wins."""
        return await self._completions.create(**{**self._options, **request})


def graphiti_client(language_model: dict[str, Any]) -> Any:
    """Return a Graphiti language model client for the configured local server.

    Goes in: the validated language_model settings. Comes out: an OpenAIGenericClient
    whose requests carry the model name, the temperature from the options, and every
    other option as a request option. Raises ConfigurationError when the settings are
    missing. It makes no request.
    """
    if language_model is None:
        raise ConfigurationError("the language_model settings are missing")
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    from openai import AsyncOpenAI

    options = language_model["options"]
    real = AsyncOpenAI(
        api_key=language_model["api_key"],
        base_url=language_model["base_url"].rstrip("/") + "/v1",
        timeout=float(language_model["request_timeout_seconds"]),
    )
    extra = {key: value for key, value in options.items() if key not in REQUEST_OPTION_KEYS_NOT_SENT}
    wrapped = SimpleNamespace(chat=SimpleNamespace(completions=_OptionedCompletions(real.chat.completions, extra)))
    config = LLMConfig(
        api_key=language_model["api_key"],
        model=language_model["model"],
        small_model=language_model["model"],
        base_url=language_model["base_url"].rstrip("/") + "/v1",
        temperature=options.get("temperature"),
    )
    return OpenAIGenericClient(config, client=wrapped)


def cognee_environment(language_model: dict[str, Any]) -> dict[str, str]:
    """Return the environment variables Cognee reads for its language model.

    Goes in: the validated language_model settings. Comes out: LLM_PROVIDER, LLM_MODEL,
    LLM_ENDPOINT, LLM_API_KEY and LLM_TEMPERATURE, plus OLLAMA_NUM_CTX when the options
    hold num_ctx. The caller sets them in the process before Cognee is imported.
    """
    if language_model is None:
        raise ConfigurationError("the language_model settings are missing")
    options = language_model["options"]
    environment = {
        "LLM_PROVIDER": language_model["provider"],
        "LLM_MODEL": language_model["model"],
        "LLM_ENDPOINT": language_model["base_url"],
        "LLM_API_KEY": language_model["api_key"],
    }
    if "temperature" in options:
        environment["LLM_TEMPERATURE"] = str(options["temperature"])
    if "num_ctx" in options:
        environment["OLLAMA_NUM_CTX"] = str(options["num_ctx"])
    return environment
