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
class Thought:
    id: str
    summaries: List[str]


@dataclass
class Message:
    role: Literal['system', 'user', 'assistant', 'tool']
    status: Literal['in_progress', 'completed', 'failed']
    content: str
    thoughts: List[Thought] = None
    actions: List[Action] = None

    action_id: str = None  # tool call id for tool messages

    prompt_tokens: int = None
    thinking_tokens: int = None
    completion_tokens: int = None

    external_id: str = None
    error: bool = False  # Set flag to false if the tool message with an error

    def openai_format(self) -> List[dict]:
        """Formats the message as a list of items for the OpenAI Client"""
        if self.role == "tool":
            return [{"call_id": self.action_id, "output": self.content, "type": "function_call_output"}]

        elif self.role == "assistant":

            thoughts = [{"id": thought.id,
                         "type": "reasoning",
                         "summary": [{"text": summary, "type": "summary_text"} for summary in thought.summaries]}
                        for thought in self.thoughts]

            tool_calls = [{"call_id": action.id, 'type': 'function_call', "name": action.name,
                           "arguments": json.dumps(action.body)} for action in self.actions]

            if self.content:
                messages = [{"id": self.external_id, "role": self.role, "content": self.content, "status": "completed",
                             "type": "message"}]
            else:
                messages = []

            return thoughts + tool_calls + messages

        return [{"role": self.role, "content": self.content}]
