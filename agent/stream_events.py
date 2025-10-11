"""Minimal streaming event protocol for observing agent execution."""
from dataclasses import dataclass
from typing import Literal, Protocol

EventType = Literal[
    "thought_start",
    "thought_delta",
    "thought_end",
    "message_delta",
    "message_end",
    "tool_call_start",
    "tool_call_end",
    "tool_result",
    "completed",
]


@dataclass
class StreamEvent:
    """A streaming event emitted during agent execution."""
    type: EventType
    text: str = ""
    name: str | None = None
    tool_id: str | None = None


class StreamSink(Protocol):
    """Protocol for consuming streaming events."""
    async def publish(self, event: StreamEvent) -> None:
        """Publish a streaming event."""
        ...
