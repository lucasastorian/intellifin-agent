import json
import uuid
from dataclasses import dataclass, field
from typing import Literal, List


@dataclass
class Action:
    id: str
    name: str
    status: Literal['streaming', 'parsed', 'completed', 'failed']
    body: dict

    external_id: str = None  # OpenAI Responses API 'id' attribute for a ResponseFunctionToolCall


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

    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def anthropic_format(self) -> dict:
        """Formats a message for the Anthropic Client"""
        if self.role == "tool":
            return {"type": "tool_result", "tool_use_id": self.action_id, "content": self.content}

        elif self.role == "assistant":
            content_blocks = []

            if self.thoughts:
                thinking_blocks = [{"type": "thinking", "thinking": thought.summaries[0],
                                    "signature": thought.id} for thought in self.thoughts]
                content_blocks += thinking_blocks

            if self.content:
                content_blocks.append({"type": "text", "text": self.content})

            if self.actions:
                action_blocks = [{"type": "tool_use", "id": action.id,
                                  "name": action.name, "input": action.body} for action in self.actions]

                content_blocks += action_blocks

            return {"role": "assistant", "content": content_blocks}

        else:
            return {"role": self.role, "content": self.content}

    def openai_format(self) -> List[dict]:
        """Formats the message as a list of items for the OpenAI Client (Responses API)"""
        # NOTE: I think there's a mistake here...  We reuse the same xternal Id across parts... That may be incorrect !
        if self.role == "tool":
            return [{"call_id": self.action_id, "output": self.content, "type": "function_call_output"}]

        elif self.role == "assistant":

            thoughts = [{"id": thought.id,
                         "type": "reasoning",
                         "summary": [{"text": summary, "type": "summary_text"} for summary in thought.summaries]}
                        for thought in self.thoughts]

            tool_calls = [{"id": action.external_id, "call_id": action.id, 'type': 'function_call', "name": action.name,
                           "arguments": json.dumps(action.body)} for action in self.actions]

            if self.content:
                messages = [{"id": self.external_id, "role": self.role, "content": self.content, "status": "completed",
                             "type": "message"}]
            else:
                messages = []

            return thoughts + tool_calls + messages

        return [{"role": self.role, "content": self.content}]
