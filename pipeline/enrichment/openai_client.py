import asyncio
from typing import Type, TypeVar
from openai import AsyncOpenAI
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


class OpenAIClient:
    """Generic OpenAI client for structured output generation"""

    def __init__(self, model: str = "gpt-5-nano", max_concurrent: int = 100):
        self.model = model
        self.client = AsyncOpenAI()
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def parse(self, system_prompt: str, user_message: str, response_model: Type[T],
                    reasoning_effort: str = "none") -> T:
        """
        Generate structured output using OpenAI's structured outputs API

        Args:
            system_prompt: System instruction for the LLM
            user_message: User input (typically includes context + content to summarize)
            response_model: Pydantic model class for structured output
            reasoning_effort: Reasoning effort level ("none", "low", "medium", "high")

        Returns:
            Parsed response matching response_model type
        """
        async with self.semaphore:
            response = await self.client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                reasoning={"effort": reasoning_effort},
                text_format=response_model
            )

            return response.output_parsed
