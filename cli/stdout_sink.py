"""Simple stdout sink for testing streaming events."""
from agent.stream_events import StreamSink, StreamEvent


class StdoutSink(StreamSink):
    """Prints streaming events to stdout with nice formatting."""

    async def publish(self, event: StreamEvent) -> None:
        """Publish event to stdout."""
        if event.type == "thought_start":
            print("\n[Thinking]\n", end="", flush=True)

        elif event.type == "thought_delta":
            print(event.content, end="", flush=True)

        elif event.type == "thought_end":
            print("\n", flush=True)

        elif event.type == "tool_call_start":
            print(f"\n[Tool: {event.tool_name}]", flush=True)

        elif event.type == "tool_call_end":
            if event.tool_name == "web_search":
                print(f"Query: {event.content}\n", flush=True)

        elif event.type == "message_delta":
            print(event.content, end="", flush=True)

        elif event.type == "message_end":
            print("\n", flush=True)

        elif event.type == "completed":
            print("\n[Completed]", flush=True)
