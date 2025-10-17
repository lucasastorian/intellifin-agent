import os
import openai
from typing import Literal, List
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from agent.message import Message
from agent.actions import BaseAction
from agent.clients.legacy_openai_client import ChatCompletionsOpenAIClient


class GroqClient(ChatCompletionsOpenAIClient):

    provider = "Groq"

    def __init__(self, model: str = "openai/gpt-oss-120b", temperature: float = 1.0,
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
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY")
        )

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None, enable_web_search: bool = False):
        """Streams a completion with the given messages"""
        messages = ([{"role": "system", "content": system_prompt}] +
                    [message.legacy_openai_format() for message in messages])

        params = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
            "tools": [action.openai_legacy_schema for action in actions],
        }

        if allowed_actions:
            params['tools'] = [action.openai_legacy_schema for action in allowed_actions]
            params['tool_choice'] = "required"

        return await self._request_with_retry(params)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.0, min=1.0, max=10.0),
        retry=retry_if_exception_type((
            openai.APIError,
            openai.BadRequestError,
            openai.APIConnectionError,
            openai.RateLimitError
        )),
        reraise=True
    )
    async def _request_with_retry(self, params: dict):
        response = await self.client.chat.completions.create(**params)
        return self._convert_response_to_message(response=response)
