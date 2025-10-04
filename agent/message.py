from dataclasses import dataclass
from typing import Literal, List


@dataclass
class Action:
    id: str
    name: str
    status: Literal['streaming', 'parsed', 'completed', 'failed']
    body: dict


@dataclass
class Message:

    role: Literal['developer', 'user', 'assistant', 'tool']
    status: Literal['in_progress', 'completed', 'failed']
    content: str
    actions: List[Action] = None

    action_id: str = None  # tool call id for tool messages

    prompt_tokens: int = None
    completion_tokens: int = None

    error: bool = False  # Set flag to false if the tool message with an error

    def format(self):
        """Formats the message for the OpenAI Client"""
        return {"role": self.role, "content": self.content}
