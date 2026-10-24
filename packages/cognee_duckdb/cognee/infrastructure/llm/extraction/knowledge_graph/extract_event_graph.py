"""Ask the language model to find the events in a text.

Translated from the removed Cognee file
`cognee/infrastructure/llm/extraction/knowledge_graph/extract_event_graph.py` (upstream commit
1513b1f41^). The removed file read the prompt's file name from `temporal_graph_prompt_path`, a
setting Cognee deleted with the flow; here the prompt is the file `generate_event_graph_prompt.txt`
that ships in this package's prompts folder, unchanged from the original.
"""

from pydantic import BaseModel

from cognee.infrastructure.llm.LLMGateway import LLMGateway
from cognee.infrastructure.llm.prompts import render_prompt

EVENT_GRAPH_PROMPT = "generate_event_graph_prompt.txt"


async def extract_event_graph(content: str, response_model: type[BaseModel]) -> BaseModel:
    """Return the events a language model finds in a text, shaped as the given model.

    It is translated from `extract_event_graph` in
    `cognee/infrastructure/llm/extraction/knowledge_graph/extract_event_graph.py`. It takes the
    text and a Pydantic model describing the answer, renders the shipped event prompt as the
    system prompt, and makes one structured-output call through Cognee's language model gateway.
    It gives back an instance of the model and never retries or repairs a bad answer.
    """
    system_prompt = render_prompt(EVENT_GRAPH_PROMPT, {})

    return await LLMGateway.acreate_structured_output(content, system_prompt, response_model)
