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
    index: int = None


@dataclass
class Thought:
    id: str
    summaries: List[str]
    index: int = None


@dataclass
class WebSearch:
    id: str
    query: str
    index: int = None


@dataclass
class Message:
    role: Literal['system', 'user', 'assistant', 'tool']
    status: Literal['in_progress', 'completed', 'failed']
    content: str
    thoughts: List[Thought] = None
    actions: List[Action] = None
    web_searches: List[WebSearch] = None

    action_id: str = None  # tool call id for tool messages

    cached_prompt_tokens: int = None  # The total number of tokens used for the prompt
    uncached_prompt_tokens: int = None  # The number of tokens of 'prompt_tokens' that were cached
    thinking_tokens: int = None  # The total number of tokens used to 'think'
    completion_tokens: int = None  # The total number of tokens for the final completion (tools calls + text)

    external_id: str = None
    content_index: int = None
    error: bool = False  # Set flag to false if the tool message with an error

    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def num_tokens(self) -> int:
        import tiktoken
        encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        return len(encoding.encode(self.content))

    def anthropic_format(self) -> dict:
        """Formats a message for the Anthropic Client"""
        if self.role == "tool":
            return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": self.action_id,
                                                 "content": self.content}]}

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

    # def openai_format(self) -> List[dict]:
    #     """Formats the message as a list of items for the OpenAI Client (Responses API)"""
    #     # NOTE: I think there's a mistake here...  We reuse the same external Id across parts... That may be incorrect !
    #     if self.role == "tool":
    #         return [{"call_id": self.action_id, "output": self.content, "type": "function_call_output"}]
    #
    #     elif self.role == "assistant":
    #
    #         thoughts = [{"id": thought.id,
    #                      "type": "reasoning",
    #                      "summary": [{"text": summary, "type": "summary_text"} for summary in thought.summaries]}
    #                     for thought in self.thoughts]
    #
    #         tool_calls = [{"id": action.external_id, "call_id": action.id, 'type': 'function_call', "name": action.name,
    #                        "arguments": json.dumps(action.body)} for action in self.actions]
    #
    #         if self.content:
    #             messages = [{"id": self.external_id, "role": self.role, "content": self.content, "status": "completed",
    #                          "type": "message"}]
    #         else:
    #             messages = []
    #
    #         return thoughts + tool_calls + messages
    #
    #     return [{"role": self.role, "content": self.content}]

    def openai_format(self) -> List[dict]:
        """Formats the message as a list of items for the OpenAI Responses API, preserving index order."""
        if self.role == "tool":
            return [{"call_id": self.action_id, "output": self.content, "type": "function_call_output"}]

        if self.role != "assistant":
            return [{"role": self.role, "content": self.content}]

        blocks = []

        if self.thoughts:
            for t in self.thoughts:
                blocks.append({
                    "type": "thought",
                    "index": t.index,
                    "value": {
                        "id": t.id,
                        "summary": [{"text": s, "type": "summary_text"} for s in t.summaries],
                        "type": "reasoning",
                    }
                })

        if self.actions:
            for a in self.actions:
                blocks.append({
                    "type": "action",
                    "index": a.index,
                    "value": {
                        "id": a.external_id,
                        "call_id": a.id,
                        "name": a.name,
                        "arguments": json.dumps(a.body),
                        "type": "function_call",
                    }
                })

        if self.content:
            blocks.append({
                "type": "message",
                "index": self.content_index,
                "value": {
                    "id": self.external_id,
                    "role": self.role,
                    "content": self.content,
                    "status": "completed",
                    "type": "message"
                }
            })

        if self.web_searches:
            for s in self.web_searches:
                blocks.append({
                    "type": "web_search",
                    "index": s.index,
                    "value": {
                        "id": s.id,
                        "action": {"query": s.query, "type": "search", "sources": None},
                        "status": "completed",
                        "type": "web_search_call"
                    }
                })

        blocks.sort(key=lambda b: b["index"])

        return [b["value"] for b in blocks]
