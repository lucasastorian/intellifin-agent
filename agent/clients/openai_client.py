import asyncio
import openai
from jiter import from_json
from openai import AsyncStream
from typing import Literal, List, Optional

from agent.actions import BaseAction
from agent.message import Message, Action, Thought, WebSearch
from agent.stream_events import StreamSink, StreamEvent


class OpenAIClient:

    def __init__(self, model: str = "gpt-5", temperature: float = 1.0,
                 reasoning_effort: Literal['minimal', 'low', 'medium', 'high'] = 'medium',
                 sink: Optional[StreamSink] = None):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self._default_sink = sink

        self.client = openai.AsyncOpenAI()

        self.tool_call_arguments = ""

    async def stream(self, messages: List[Message], system_prompt: str, actions: List[BaseAction],
                     allowed_actions: List[BaseAction] = None, enable_web_search: bool = False,
                     sink: Optional[StreamSink] = None, abort: Optional[asyncio.Event] = None):
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

        response = await self.client.responses.create(**params)
        return await self.stream_completion(response=response, sink=sink or self._default_sink, abort=abort)

    async def stream_completion(self, response: AsyncStream, sink: Optional[StreamSink] = None, abort: Optional[asyncio.Event] = None):
        """Streams a chat completion to the console or a custom sink"""
        completion = Message(role="assistant", status="in_progress", content="", thoughts=[], actions=[], web_searches=[])

        event_count = 0
        async for event in response:
            event_count += 1
            # Check if abort was requested
            if abort and abort.is_set():
                break

            if event.type == 'response.created':
                pass

            elif event.type == 'response.in_progress':
                pass

            elif event.type == 'response.output_item.added':

                if event.item.type == 'reasoning':
                    completion.thoughts.append(Thought(id=event.item.id, summaries=[], index=event.output_index))
                    if sink:
                        await sink.publish(StreamEvent(type="thought_start"))
                    else:
                        print(f"Thinking: \n\n", sep="", end="")

                elif event.item.type == 'function_call':
                    self.tool_call_arguments = ""
                    action = Action(id=event.item.call_id, name=event.item.name, status="streaming", body={},
                                    external_id=event.item.id, index=event.output_index)
                    completion.actions.append(action)
                    if sink:
                        await sink.publish(StreamEvent(type="tool_call_start", name=event.item.name, tool_id=event.item.call_id))

                elif event.item.type == 'message':
                    completion.external_id = event.item.id
                    completion.content_index = event.output_index

                elif event.item.type == 'web_search_call':
                    web_search = WebSearch(id=event.item.id, query="", index=event.output_index)
                    completion.web_searches.append(web_search)
                    if sink:
                        await sink.publish(StreamEvent(type="tool_call_start", name="web_search", tool_id=event.item.id))
                    else:
                        print("Searching Web: ", sep="", end="")
                    # print(event.item)

            elif event.type == 'response.reasoning_summary_part.added':
                completion.thoughts[-1].summaries.append("")
                if not sink:
                    print("- ", sep="", end="")

            elif event.type == 'response.reasoning_summary_text.delta':
                completion.thoughts[-1].summaries[-1] += event.delta
                if sink:
                    await sink.publish(StreamEvent(type="thought_delta", text=event.delta))
                else:
                    print(event.delta, sep="", end="")

            elif event.type == 'response.reasoning_summary_text.done':
                # Identical to previous summary deltas
                completion.thoughts[-1].summaries[-1] = event.text
                if sink:
                    await sink.publish(StreamEvent(type="thought_end"))
                else:
                    print("\n\n")

            elif event.type == 'response.reasoning_summary_part.done':
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
                if sink:
                    await sink.publish(StreamEvent(type="message_delta", text=event.delta))
                else:
                    print(event.delta, sep="", end="")

            elif event.type == 'response.output_text.done':
                pass

            elif event.type == 'response.content_part.done':
                pass

            elif event.type == 'response.output_item.done':

                if event.item.type == 'web_search_call':
                    completion.web_searches[-1].query = event.item.action.query
                    if sink:
                        await sink.publish(StreamEvent(type="tool_call_end", name="web_search", text=event.item.action.query))
                    else:
                        print(f"{event.item.action.query}\n\n")
                    # print(event.item)

            elif event.type == 'response.completed':
                usage = event.response.usage

                completion.prompt_tokens = usage.input_tokens
                completion.thinking_tokens = usage.output_tokens_details.reasoning_tokens
                completion.completion_tokens = usage.output_tokens - completion.thinking_tokens

        completion.status = 'completed'

        # Don't publish "completed" here - it will be published by the agent runner
        # after all tools are executed, not just when streaming finishes

        return completion
