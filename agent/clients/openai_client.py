import openai
from jiter import from_json
from typing import Literal, List
from openai._streaming import AsyncStream
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall

from agent.actions import BaseAction
from agent.message import Message, Action


class OpenAIClient:

    def __init__(self, model: str = "gpt-5-mini", temperate: int = 1, provider: Literal['OpenAI'] = 'OpenAI'):
        self.model = model
        self.temperature = temperate
        self.provider = provider

        self.client = openai.AsyncOpenAI()

        self.tool_call_arguments = ""

    async def stream(self, messages: List[Message], actions: List[BaseAction]):
        """Streams a completion with the given messages"""
        messages = [message.format() for message in messages]

        params = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
            "tools": [action.openai_schema for action in actions],
            "stream": True
        }

        response = await self.client.chat.completions.create(**params)
        return await self.stream_completion(response=response)

    async def stream_completion(self, response: AsyncStream):
        """Streams a chat completion to the console"""
        completion = Message(role="assistant", status="in_progress", content="")

        async for chunk in response:
            if chunk.choices:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    completion.content += content
                    print(content, sep="", end="")

                if json_chunk := chunk.choices[0].delta.tool_calls:
                    tool_call_chunk = json_chunk[0]
                    function_name = tool_call_chunk.function.name

                    if function_name:
                        completion = self._create_action(completion=completion, tool_call=tool_call_chunk)
                    else:
                        completion = self._parse_action(completion=completion, tool_call=tool_call_chunk)

            if chunk.usage:
                completion.prompt_tokens = chunk.usage.prompt_tokens
                completion.completion_tokens = chunk.usage.completion_tokens

        completion.status = "completed"

        # Add newline after streaming completes
        if completion.content:
            print()

        return completion

    def _create_action(self, completion: Message, tool_call: ChoiceDeltaToolCall):
        """Creates a new action"""
        if completion.actions is None:
            completion.actions = []

        action = Action(id=tool_call.id, name=tool_call.function.name, status="streaming", body={})
        completion.actions.append(action)

        self.tool_call_arguments = ""

        if tool_call.function.arguments:
            return self._parse_action(completion=completion, tool_call=tool_call)

        return completion

    def _parse_action(self, completion: Message, tool_call: ChoiceDeltaToolCall):
        """Parsing action"""
        self.tool_call_arguments += tool_call.function.arguments

        task_json = from_json((self.tool_call_arguments.strip() or "{}").encode(), partial_mode="trailing-strings")

        if type(task_json) is not dict:
            return

        completion.actions[-1].body = task_json

        return completion
