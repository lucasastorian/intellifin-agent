import anthropic
from jiter import from_json
from anthropic import AsyncStream
from typing import Literal, List

from agent.actions import BaseAction
from agent.message import Message, Action, Thought


class AnthropicClient:

    max_tokens: int = 16384
    betas: List[str] = ["interleaved-thinking-2025-05-14"]

    def __init__(self, model: str = "claude-sonnet-4-5-20250929", temperature: float = 1.0,
                 reasoning_effort: Literal['low', 'medium', 'high', 'none'] = 'medium'):
        self.model = model
        self.temperature = temperature
        self.reasoning_budget = {
            "low": 1024,
            "medium": 2048,
            "high": 4096,
            "none": 0
        }[reasoning_effort]

        self.client = anthropic.AsyncAnthropic()

        self.tool_call_arguments = ""

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None):
        """Streams a completion with the given messages"""
        messages = [message.anthropic_format() for message in messages]

        params = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "messages": messages,
            "betas": self.betas,
            "tools": [action.anthropic_schema for action in actions],
            "stream": True
        }

        if self.reasoning_budget != "none":
            params['thinking'] = {
                "type": "enabled",
                "budget_tokens": self.reasoning_budget
            }

        if allowed_actions:
            params["tools"] = [action.anthropic_schema for action in allowed_actions]
            params['tool_choice'] = {"type": "tool"}

        response = await self.client.messages.create(**params)

        return await self.stream_completion(response=response)

    async def stream_completion(self, response: AsyncStream):
        """Streams the Anthropic Completion"""
        completion = Message(role="assistant", status="in_progress", content="", thoughts=[], actions=[])

        async for event in response:

            if event.type == 'message_start':
                pass

            elif event.type == 'content_block_start':
                if event.content_block.type == 'thinking':
                    completion.thoughts.append(Thought(id="", summaries=[""]))

                elif event.content_block.type == 'text':
                    pass

                elif event.cont_block.type == 'tool_use':
                    self.tool_call_arguments = ""
                    action = Action(id=event.content_block.id, name=event.content_block.name,
                                    status="streaming", body={})
                    completion.actions.append(action)

            elif event.type == 'content_block_delta':
                if event.delta.type == 'thinking_delta':
                    completion.thoughts[-1].summaries[0] += event.delta.thinking

                elif event.delta.type == 'signature_delta':
                    completion.thoughts[-1].id += event.delta.signature

                elif event.delta.type == 'input_json_delta':
                    self.tool_call_arguments += event.delta.partial_json
                    body_json = from_json((self.tool_call_arguments.strip() or "{}").encode(),
                                          partial_mode="trailing-strings")

                    if type(body_json) is not dict:
                        continue

                    completion.actions[-1].body = body_json

                elif event.delta.type == 'text_delta':
                    completion.content += event.delta.text

            elif event.type == 'content_block_stop':
                pass

            elif event.type == 'message_delta':
                usage = event.usage
                completion.prompt_tokens = usage.input_tokens
                completion.completion_tokens = usage.output_tokens

                if completion.actions:
                    completion.actions[-1].status = 'parsed'

            elif event.type == 'message_stop':
                pass

        completion.status = 'completed'

        return completion
