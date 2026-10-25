"""Ask the language model which entities take part in each event of a list.

Translated from the removed Cognee file
`cognee/infrastructure/llm/extraction/extract_event_entities.py` (upstream commit 1513b1f41^).
The removed file read the prompt's file name from `event_entity_prompt_path`, a setting Cognee
deleted with the flow; here the prompt is the file `generate_event_entity_prompt.txt` that ships
in this package's prompts folder, unchanged from the original.
"""

from pydantic import BaseModel

from cognee.infrastructure.llm.LLMGateway import LLMGateway
from cognee.infrastructure.llm.prompts.render_prompt import render_prompt

EVENT_ENTITY_PROMPT = "generate_event_entity_prompt.txt"


async def extract_event_entities(content: str, response_model: type[BaseModel]):
    """Return the entities the language model finds around each event, shaped as the given model.

    It is translated from `extract_event_entities` in
    `cognee/infrastructure/llm/extraction/extract_event_entities.py`. It takes the events as
    JSON text and a Pydantic model describing the answer, renders the shipped entity prompt as
    the system prompt, and makes one structured-output call through Cognee's language model
    gateway. It gives back an instance of the model and never retries or repairs a bad answer.
    """
    system_prompt = render_prompt(EVENT_ENTITY_PROMPT, {})

    return await LLMGateway.acreate_structured_output(content, system_prompt, response_model)
