import json
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
    role: Literal['system', 'user', 'assistant', 'tool']
    status: Literal['in_progress', 'completed', 'failed']
    content: str
    actions: List[Action] = None

    action_id: str = None  # tool call id for tool messages

    prompt_tokens: int = None
    completion_tokens: int = None

    error: bool = False  # Set flag to false if the tool message with an error

    def format(self):
        """Formats the message for the OpenAI Client"""
        if self.role == "tool":
            return {"role": self.role, "content": self.content, "tool_call_id": self.action_id}

        elif self.role == "assistant":
            tool_calls = [{"id": action.id, 'type': 'function',
                           "function": {"name": action.name, "arguments": json.dumps(action.body)}} for action in
                          self.actions]

            return {"role": self.role, "content": self.content, "tool_calls": tool_calls}

        return {"role": self.role, "content": self.content}
