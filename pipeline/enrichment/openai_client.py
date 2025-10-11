import asyncio
import time
from typing import Type
from openai import AsyncOpenAI

from pipeline.enrichment.llm_cache import LLMCache
from pipeline.enrichment.base_client import BaseLLMClient, T


class OpenAIClient(BaseLLMClient):
    """Generic OpenAI client for structured output generation"""

    reasoning_effort: str = "minimal"

    def __init__(self, model: str = "gpt-5-nano", max_concurrent: int = 8, cache: bool = True, timeout: int = 180):
        self.model = model
        self.client = AsyncOpenAI()
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.cache = LLMCache() if cache else None
        self.timeout = timeout

    async def parse(self, system_prompt: str, user_message: str, response_model: Type[T]) -> T:
        """Generate structured output using OpenAI streaming with minimal reasoning"""
        if self.cache:
            cached = self.cache.get(
                model=self.model,
                system_prompt=system_prompt,
                user_message=user_message,
                response_model=response_model,
                reasoning_effort=self.reasoning_effort
            )

            if cached is not None:
                print(f"[OpenAI Cache Hit] {self.model}")
                return cached

        start_time = time.time()
        async with self.semaphore:
            params = {
                "model": self.model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "text_format": response_model,
                "reasoning": {"effort": self.reasoning_effort},
            }

            async def _do_request():
                async with self.client.responses.stream(**params) as stream:
                    async for event in stream:
                        if event.type == "response.error":
                            raise Exception(f"OpenAI error: {event.error}")
                        elif event.type == "response.completed":
                            break

                    final_response = await stream.get_final_response()
                    return final_response.output_parsed

            result = await asyncio.wait_for(_do_request(), timeout=self.timeout)
            duration = time.time() - start_time

            # print(f"[OpenAI Request] {self.model} - {duration:.2f}s (reasoning_effort={self.reasoning_effort})")

            if self.cache:
                self.cache.set(
                    model=self.model,
                    system_prompt=system_prompt,
                    user_message=user_message,
                    response_model=response_model,
                    reasoning_effort=self.reasoning_effort,
                    response=result
                )

            return result
