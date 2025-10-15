import os
import openai
from typing import Literal

from agent.clients.openai_client import OpenAIClient


class XAIClient(OpenAIClient):
    """xAI Grok client using OpenAI-compatible API"""

    provider = "XAI"

    def __init__(self, model: str = "grok-4-0709", temperature: float = 1.0,
                 reasoning_effort: Literal['minimal', 'low', 'medium', 'high'] = 'medium',
                 verbose: bool = True,
                 tier: str = "tier-3"):
        super().__init__(
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            verbose=verbose,
            tier=tier
        )

        self.client = openai.AsyncOpenAI(
            base_url="https://api.x.ai/v1",
            api_key=os.getenv("XAI_API_KEY")
        )
