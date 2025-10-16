import os
import openai
from jiter import from_json
from openai import AsyncStream
from typing import Literal, List

from agent.actions import BaseAction
from agent.message import Message, Action, Thought, WebSearch
from agent.clients.base_client import BaseClient


class OpenAIClient(BaseClient):

    provider: str = "OpenAI"

    def __init__(self, model: str = "gpt-5", temperature: float = 1.0,
                 reasoning_effort: Literal['minimal', 'low', 'medium', 'high'] = 'medium',
                 verbose: bool = True,
                 tier: str = "tier-3"):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.verbose = verbose
        self.tier = tier  # Not yet used for rate limiting, but reserved for future

        self.client = openai.AsyncOpenAI(
            base_url="https://api.openai.com/v1",
            api_key=os.environ['OPENAI_API_KEY'],
            timeout=300.0,
        )

        self.tool_call_arguments = ""

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None, enable_web_search: bool = False):
        """Streams a completion with the given messages"""
        items = [item for message in messages for item in message.openai_format()]

        params = {
            "model": self.model,
            "instructions": system_prompt,
            "input": items,
            "reasoning": {"effort": self.reasoning_effort, "summary": "auto"},
            "tools": [action.openai_schema for action in actions],
            "stream": True
        }

        if enable_web_search:
            params['tools'].append({"type": "web_search"})

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

        stream = await self.client.responses.create(**params)

        return await self.stream_completion(response=stream)

    async def stream_completion(self, response: AsyncStream):
        """Streams a chat completion to the console"""
        completion = Message(role="assistant", status="in_progress", content="", thoughts=[], actions=[], web_searches=[])

        async for event in response:

            if event.type == 'response.created':
                pass

            elif event.type == 'response.in_progress':
                pass

            elif event.type == 'response.output_item.added':

                if event.item.type == 'reasoning':
                    completion.thoughts.append(Thought(id=event.item.id, summaries=[], index=event.output_index))
                    if self.verbose:
                        print(f"Thinking: \n\n", sep="", end="")

                elif event.item.type == 'function_call':
                    self.tool_call_arguments = ""
                    action = Action(id=event.item.call_id, name=event.item.name, status="streaming", body={},
                                    external_id=event.item.id, index=event.output_index)
                    completion.actions.append(action)

                elif event.item.type == 'message':
                    completion.external_id = event.item.id
                    completion.content_index = event.output_index

                elif event.item.type == 'web_search_call':
                    web_search = WebSearch(id=event.item.id, query="", index=event.output_index)
                    completion.web_searches.append(web_search)
                    if self.verbose:
                        print("Searching Web: ", sep="", end="")
                    # print(event.item)

            elif event.type == 'response.reasoning_summary_part.added':
                completion.thoughts[-1].summaries.append("")
                if self.verbose:
                    print("- ", sep="", end="")

            elif event.type == 'response.reasoning_summary_text.delta':
                completion.thoughts[-1].summaries[-1] += event.delta
                if self.verbose:
                    print(event.delta, sep="", end="")

            elif event.type == 'response.reasoning_summary_text.done':
                # Identical to previous summary deltas
                completion.thoughts[-1].summaries[-1] = event.text
                if self.verbose:
                    print("\n\n")

            elif event.type == 'response.reasoning_summary_part.done':
                # Identical to previous summary deltas
                completion.thoughts[-1].summaries[-1] = event.part.text

            elif event.type == 'response.function_call_arguments.delta':
                self.tool_call_arguments += event.delta
                try:
                    body_json = from_json((self.tool_call_arguments.strip() or "{}").encode(),
                                          partial_mode="trailing-strings")

                    if type(body_json) is not dict:
                        continue

                    completion.actions[-1].body = body_json

                except ValueError:
                    continue

            elif event.type == 'response.function_call_arguments.done':
                completion.actions[-1].status = 'parsed'

            elif event.type == 'response.output_text.delta':
                completion.content += event.delta
                if self.verbose:
                    print(event.delta, sep="", end="")

            elif event.type == 'response.output_text.done':
                pass

            elif event.type == 'response.content_part.done':
                pass

            elif event.type == 'response.output_item.done':

                if event.item.type == 'web_search_call':
                    completion.web_searches[-1].query = event.item.action.query
                    if self.verbose:
                        print(f"{event.item.action.query}\n\n")

            elif event.type == 'response.completed':
                usage = event.response.usage

                completion.uncached_prompt_tokens = usage.input_tokens - usage.input_tokens_details.cached_tokens
                completion.cached_prompt_tokens = usage.input_tokens_details.cached_tokens
                completion.thinking_tokens = usage.output_tokens_details.reasoning_tokens
                completion.completion_tokens = usage.output_tokens - completion.thinking_tokens

        completion.status = 'completed'

        return completion
