import asyncio
import logging
from typing import Type, TypeVar
from openai import AsyncOpenAI
from pydantic import BaseModel
from .llm_cache import LLMCache

T = TypeVar('T', bound=BaseModel)


class OpenAIClient:
    """Generic OpenAI client for structured output generation"""

    def __init__(self, model: str = "gpt-5-nano", max_concurrent: int = 100, cache: bool = True):
        self.model = model
        self.client = AsyncOpenAI()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.cache = LLMCache() if cache else None

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
        # Check cache first
        if self.cache:
            cached = self.cache.get(
                model=self.model,
                system_prompt=system_prompt,
                user_message=user_message,
                response_model=response_model,
                reasoning_effort=reasoning_effort
            )
            if cached is not None:
                logging.debug("LLM cache hit")
                return cached

        async with self.semaphore:
            # Build request params
            params = {
                "model": self.model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "text_format": response_model
            }

            # Only include reasoning if effort is not "none"
            if reasoning_effort and reasoning_effort != "none":
                params["reasoning"] = {"effort": reasoning_effort}

            response = await self.client.responses.parse(**params)

            result = response.output_parsed

            # Cache the result
            if self.cache:
                self.cache.set(
                    model=self.model,
                    system_prompt=system_prompt,
                    user_message=user_message,
                    response_model=response_model,
                    reasoning_effort=reasoning_effort,
                    response=result
                )

            return result
