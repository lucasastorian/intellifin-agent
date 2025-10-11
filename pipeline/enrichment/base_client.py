from abc import ABC, abstractmethod
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients"""

    @abstractmethod
    async def parse(self, system_prompt: str, user_message: str, response_model: Type[T]) -> T:
        """
        Generate structured output using the LLM's API

        Args:
            system_prompt: System instruction for the LLM
            user_message: User input (typically includes context + content to summarize)
            response_model: Pydantic model class for structured output

        Returns:
            Parsed response matching response_model type
        """
        pass
