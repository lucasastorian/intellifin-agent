import os
import json
import openai
from openai.types.chat.chat_completion import ChatCompletion
from typing import Literal, List

from agent.message import Message, Action
from agent.actions.base_action import BaseAction
from agent.clients.legacy_openai_client import ChatCompletionsOpenAIClient


class XAIClient(ChatCompletionsOpenAIClient):
    """xAI Grok client using OpenAI-compatible API"""

    # NOTE: XAI does NOT accept return usage statistics when streaming, so we're just going to disable streaming

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

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None, enable_web_search: bool = False):
        """Streams a completion with the given messages"""
        # NO support for reasoning effort in XAI client
        messages = ([{"role": "system", "content": system_prompt}] +
                    [message.legacy_openai_format() for message in messages])

        params = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
            "tools": [action.openai_legacy_schema for action in actions],
            # "stream": True
        }

        if allowed_actions:
            params['tools'] = [action.openai_legacy_schema for action in allowed_actions]
            params['tool_choice'] = "required"

        response = await self.client.chat.completions.create(**params)

        return self._convert_response_to_message(response=response)

        # return await self.stream_completion(response=response)

    @staticmethod
    def _convert_response_to_message(response: ChatCompletion):
        """Converts an OpenAI response to a Message object"""
        content = response.choices[0].message.content
        cached_tokens = response.usage.prompt_tokens_details.cached_tokens
        uncached_tokens = response.usage.prompt_tokens - cached_tokens
        thinking_tokens = response.usage.completion_tokens_details.reasoning_tokens
        completion_tokens = response.usage.completion_tokens

        if response.choices[0].message.tool_calls:
            actions = [Action(id=call.id, name=call.function.name, status="parsed",
                              body=json.loads(call.function.arguments))
                       for call in response.choices[0].message.tool_calls]

            return Message(role="assistant", content=content, status="completed", actions=actions,
                           cached_prompt_tokens=response.usage.prompt_tokens_details.cached_tokens,
                           uncached_prompt_tokens=uncached_tokens, thinking_tokens=thinking_tokens,
                           completion_tokens=completion_tokens)

        return Message(content=content, role="assistant", status="completed",
                       cached_prompt_tokens=response.usage.prompt_tokens_details.cached_tokens,
                       uncached_prompt_tokens=uncached_tokens, thinking_tokens=thinking_tokens,
                       completion_tokens=completion_tokens)
