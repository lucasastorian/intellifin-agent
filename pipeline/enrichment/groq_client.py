import asyncio
import json
import logging
from typing import Type, TypeVar
from groq import AsyncGroq
from pydantic import BaseModel
from .llm_cache import LLMCache
from .base_client import BaseLLMClient, T


class GroqClient(BaseLLMClient):
    """Groq client for structured output generation"""

    def __init__(self, model: str = "openai/gpt-oss-20b", max_concurrent: int = 100, cache: bool = True, timeout: float = 10.0):
        self.model = model
        self.client = AsyncGroq(timeout=timeout)
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.cache = LLMCache() if cache else None

    async def parse(self, system_prompt: str, user_message: str, response_model: Type[T],
                    reasoning_effort: str = "none") -> T:
        """
        Generate structured output using Groq's structured outputs API

        Args:
            system_prompt: System instruction for the LLM
            user_message: User input (typically includes context + content to summarize)
            response_model: Pydantic model class for structured output
            reasoning_effort: Reasoning effort level (ignored for Groq, kept for API compatibility)

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
            start_time = asyncio.get_event_loop().time()

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__.lower(),
                        "schema": response_model.model_json_schema()
                    }
                }
            )

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > 5:
                logging.warning(f"Slow Groq call: {elapsed:.1f}s for {self.model}")

            result = response_model.model_validate(json.loads(response.choices[0].message.content))

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
