"""A language-model client for OpenAI chat completions that can ask for structured (JSON schema) output.

Goes in: messages and optional tools; comes out: the reply text or tool calls. It calls a remote
service.
"""

import os
from typing import Dict, List, Optional

from openai import OpenAI

from mem0.configs.llms.base import BaseLlmConfig
from mem0.llms.base import LLMBase


class OpenAIStructuredLLM(LLMBase):
    """Chat client for OpenAI with structured output support.

    Goes in: messages, optional response format and tools; comes out: the reply text or tool calls.
    It keeps no conversation state.
    """

    def __init__(self, config: Optional[BaseLlmConfig] = None):
        """Set up the OpenAI client, defaulting the model name when none is given, with the key and base address from the config or environment.

        Goes in: an optional config; comes out: a ready client. It sends no request.
        """
        super().__init__(config)

        if not self.config.model:
            self.config.model = "gpt-5-mini"

        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        base_url = (
            self.config.openai_base_url
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        tool_choice: str = "auto",
        response_format: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Generate a response based on the given messages using OpenAI.

        Args:
            messages (List[Dict[str, str]]): A list of dictionaries, each containing a 'role' and 'content' key.
            response_format (Optional[str]): The desired format of the response. Defaults to None.


        Returns:
            str: The generated response.
        """
        if kwargs:
            raise TypeError(
                f"generate_response() got unexpected keyword arguments: {sorted(kwargs)}"
            )
        params = self._get_supported_params(messages=messages)
        params["model"] = self.config.model

        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        response = self.client.beta.chat.completions.parse(**params)
        return response.choices[0].message.content
