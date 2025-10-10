import openai
from jiter import from_json
from openai import AsyncStream
from typing import Literal, List

from agent.actions import BaseAction
from agent.message import Message, Action, Thought


class OpenAIClient:

    def __init__(self, model: str = "gpt-5", temperature: float = 1.0,
                 reasoning_effort: Literal['low', 'medium', 'high', 'none'] = 'medium'):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort

        self.client = openai.AsyncOpenAI()

        self.tool_call_arguments = ""

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None):
        """Streams a completion with the given messages"""
        items = [item for message in messages for item in message.openai_format()]

        # print(items)

        params = {
            "model": self.model,
            "temperature": self.temperature,
            "instructions": system_prompt,
            "input": items,
            "reasoning": {"effort": self.reasoning_effort, "summary": "auto"},
            "tools": [action.openai_schema for action in actions],
            "stream": True
        }

        if allowed_actions and len(allowed_actions) == 1:
            params['tool_choice'] = {"type": "function", "name": allowed_actions[0].name}

        elif allowed_actions and len(allowed_actions) > 1:
            params['tool_choice'] = {
                "type": "allowed_tools",
                "mode": "auto",
                "tools": [
                    {"type": "function", "name": action.name}
                    for action in allowed_actions
                ]
            }

        response = await self.client.responses.create(**params)
        return await self.stream_completion(response=response)

    async def stream_completion(self, response: AsyncStream):
        """Streams a chat completion to the console"""
        completion = Message(role="assistant", status="in_progress", content="", thoughts=[], actions=[])

        async for event in response:

            if event.type == 'response.created':
                pass

            elif event.type == 'response.in_progress':
                pass

            elif event.type == 'response.output_item.added':

                if event.item.type == 'reasoning':
                    completion.thoughts.append(Thought(id=event.item.id, summaries=[]))

                if event.item.type == 'function_call':
                    self.tool_call_arguments = ""
                    action = Action(id=event.item.call_id, name=event.item.name, status="streaming", body={},
                                    external_id=event.item.id)
                    completion.actions.append(action)

                if event.item.type == 'message':
                    completion.external_id = event.item.id

            elif event.type == 'response.reasoning_summary_part.added':
                completion.thoughts[-1].summaries.append("")

            elif event.type == 'response.reasoning_summary_text.delta':
                completion.thoughts[-1].summaries[-1] += event.delta

            elif event.type == 'response.reasoning_summary_text.done':
                # Identical to previous summary deltas
                completion.thoughts[-1].summaries[-1] = event.text

            elif event.type == 'response.reasoning_summary_part.done':
                # Identical to previous summary deltas
                completion.thoughts[-1].summaries[-1] = event.part.text

            elif event.type == 'response.function_call_arguments.delta':
                self.tool_call_arguments += event.delta
                body_json = from_json((self.tool_call_arguments.strip() or "{}").encode(),
                                      partial_mode="trailing-strings")

                if type(body_json) is not dict:
                    continue

                completion.actions[-1].body = body_json

            elif event.type == 'response.function_call_arguments.done':
                completion.actions[-1].status = 'parsed'

            elif event.type == 'response.output_text.delta':
                completion.content += event.delta
                print(event.delta, sep="", end="")

            elif event.type == 'response.output_text.done':
                pass

            elif event.type == 'response.content_part.done':
                pass

            elif event.type == 'response.output_item.done':
                pass

            elif event.type == 'response.completed':
                usage = event.response.usage

                completion.prompt_tokens = usage.input_tokens
                completion.thinking_tokens = usage.output_tokens_details.reasoning_tokens
                completion.completion_tokens = usage.output_tokens - completion.thinking_tokens

        completion.status = 'completed'

        return completion
