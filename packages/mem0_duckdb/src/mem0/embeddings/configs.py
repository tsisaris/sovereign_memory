"""Declare an embedding provider and its independent model revision metadata."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class EmbedderConfig(BaseModel):
    """Select the provider and keep its declared model revision separate from SDK options."""

    provider: str = Field(
        description="Provider of the embedding model (e.g., 'ollama', 'openai')",
        default="openai",
    )
    config: Optional[dict] = Field(
        description="Configuration for the specific embedding model", default={}
    )
    revision: Optional[str] = Field(
        default=None,
        description="Declared model revision or artifact digest; required by the Owner tier",
    )

    @field_validator("config")
    def validate_config(cls, v, values):
        """Keep provider SDK options only for an explicitly supported provider."""
        provider = values.data.get("provider")
        if provider in [
            "openai",
            "ollama",
            "huggingface",
            "azure_openai",
            "gemini",
            "vertexai",
            "together",
            "lmstudio",
            "langchain",
            "aws_bedrock",
            "fastembed",
        ]:
            return v
        else:
            raise ValueError(f"Unsupported embedding provider: {provider}")
