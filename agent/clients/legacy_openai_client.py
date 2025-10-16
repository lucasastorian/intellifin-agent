import json
import openai
from jiter import from_json
from typing import Literal, List
from openai._streaming import AsyncStream
from openai.types.chat.chat_completion import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall

from agent.actions import BaseAction
from agent.message import Message, Action
from agent.clients.base_client import BaseClient


class ChatCompletionsOpenAIClient(BaseClient):

    provider: str = "OpenAI"

    def __init__(self, model: str = "gpt-5", temperature: float = 1.0,
                 reasoning_effort: Literal['minimal', 'low', 'medium', 'high'] = 'medium',
                 verbose: bool = True,
                 tier: str = "tier-3"):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.verbose = verbose
        self.tier = tier

        self.client = openai.AsyncOpenAI()

        self.tool_call_arguments = ""

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None, enable_web_search: bool = False):
        """Streams a completion with the given messages"""
        messages = [{"role": "system", "content": system_prompt}] + [message.legacy_openai_format() for message in messages]

        params = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": messages,
            "tools": [action.openai_legacy_schema for action in actions],
            "reasoning_effort": self.reasoning_effort,
            "stream": True
        }

        if allowed_actions:
            params['tools'] = [action.openai_legacy_schema for action in allowed_actions]
            params['tool_choice'] = "required"

        response = await self.client.chat.completions.create(**params)
        return await self.stream_completion(response=response)

    async def stream_completion(self, response: AsyncStream):
        """Streams a chat completion to the console"""
        completion = Message(role="assistant", status="in_progress", content="")

        async for chunk in response:
            if chunk.choices:
                print(chunk.choices[0])
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

    @staticmethod
    def _convert_response_to_message(response: ChatCompletion):
        """Converts an OpenAI response to a Message object"""
        content = response.choices[0].message.content
        if response.usage.prompt_tokens_details:
            cached_tokens = response.usage.prompt_tokens_details.cached_tokens
        else:
            cached_tokens = 0

        uncached_tokens = response.usage.prompt_tokens - cached_tokens
        if response.usage.completion_tokens_details:
            thinking_tokens = response.usage.completion_tokens_details.reasoning_tokens
        else:
            thinking_tokens = 0

        completion_tokens = response.usage.completion_tokens

        if response.choices[0].message.tool_calls:
            actions = [Action(id=call.id, name=call.function.name, status="parsed",
                              body=json.loads(call.function.arguments))
                       for call in response.choices[0].message.tool_calls]

            return Message(role="assistant", content=content, status="completed", actions=actions,
                           cached_prompt_tokens=cached_tokens,
                           uncached_prompt_tokens=uncached_tokens, thinking_tokens=thinking_tokens,
                           completion_tokens=completion_tokens)

        return Message(content=content, role="assistant", status="completed",
                       cached_prompt_tokens=cached_tokens,
                       uncached_prompt_tokens=uncached_tokens, thinking_tokens=thinking_tokens,
                       completion_tokens=completion_tokens)
