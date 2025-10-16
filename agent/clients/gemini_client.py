import os
import openai
from typing import Literal, List

from agent.actions import BaseAction
from agent.message import Message
from agent.clients.legacy_openai_client import ChatCompletionsOpenAIClient


class GeminiClient(ChatCompletionsOpenAIClient):
    provider: str = "Google"

    def __init__(self, model: str = "gemini-2.5-pro", temperature: float = 1.0,
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

        self.reasoning_budget = {
            "low": 1024,
            "medium": 2048,
            "high": 4096,
            "none": 0
        }[self.reasoning_effort]

        self.client = openai.AsyncOpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=os.getenv("GEMINI_API_KEY")
        )

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None, enable_web_search: bool = False):
        """Streams a completion with the given messages"""
        messages = [{"role": "system", "content": system_prompt}] + [message.legacy_openai_format() for message in
                                                                     messages]

        params = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
            "tools": [action.openai_legacy_schema for action in actions],
            "stream": True,
            "extra_body": {
                "google": {
                    "thinking_config": {
                        "thinking_budget": self.reasoning_budget,
                        "include_thoughts": True
                    }
                }
            }
        }

        if allowed_actions:
            params['tools'] = [action.openai_legacy_schema for action in allowed_actions]
            params['tool_choice'] = "required"

        response = await self.client.chat.completions.create(**params)
        return await self.stream_completion(response=response)
