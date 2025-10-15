import tiktoken
from abc import ABC, abstractmethod
from typing import List

from agent.actions import BaseAction
from agent.message import Message


class BaseClient(ABC):
    """Abstract base class for LLM clients (OpenAI, Anthropic, etc.)"""

    model: str = None
    reasoning_effort: str = None

    @abstractmethod
    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None, enable_web_search: bool = False) -> Message:
        """
        Stream a completion with the given messages and actions.

        Args:
            messages: Conversation history
            system_prompt: System instructions
            actions: Available actions/tools
            allowed_actions: Optional subset of actions to force selection from
            enable_web_search: Whether to enable web search capability

        Returns:
            Message: Completed assistant message with content, actions, and thoughts
        """
        raise NotImplementedError

    @staticmethod
    def num_tokens(content: str) -> int:
        encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        return len(encoding.encode(content))
