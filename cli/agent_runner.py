"""Agent runner that bridges the agent with UI via streaming events."""
import asyncio
import logging
from agent.agent import Agent
from agent.stream_events import StreamEvent, StreamSink

LOG = logging.getLogger("intellifin.runner")


class QueueSink(StreamSink):
    """Sink that publishes events to an asyncio queue for UI consumption."""

    def __init__(self, queue: asyncio.Queue[StreamEvent]) -> None:
        self.queue = queue

    async def publish(self, event: StreamEvent) -> None:
        """Publish event to the queue."""
        await self.queue.put(event)


class AgentRunner:
    """Manages agent execution with abort support and event streaming."""

    def __init__(self, agent: Agent):
        self.agent = agent
        self.queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self.abort = asyncio.Event()
        self.task: asyncio.Task | None = None

    def attach(self):
        """Attach the queue sink and abort event to the agent's client."""
        sink = QueueSink(self.queue)
        self.agent.client._default_sink = sink
        self.agent.sink = sink  # Also set on agent for tool results
        self.agent.abort = self.abort

    async def ask(self, query: str) -> None:
        """Start agent execution in the background."""
        self.abort.clear()
        self.attach()
        # Run the agent in the background so UI can consume events
        self.task = asyncio.create_task(self._run_with_error_handling(query))

    async def _run_with_error_handling(self, query: str) -> None:
        """Run agent with error handling and publish errors to the queue."""
        import traceback
        try:
            await self.agent.run(query)
            # Publish completion event after agent finishes successfully
            from agent.stream_events import StreamEvent
            await self.queue.put(StreamEvent(type="completed"))
        except Exception as e:
            LOG.exception(f"Agent execution error: {e}")

            # Publish error with traceback to UI
            from agent.stream_events import StreamEvent
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            await self.queue.put(StreamEvent(
                type="message_delta",
                text=f"Error: {str(e)}\n\n{tb_str}\n"
            ))
            await self.queue.put(StreamEvent(type="completed"))

    def cancel(self) -> None:
        """Cancel the current agent execution."""
        self.abort.set()
        if self.task and not self.task.done():
            self.task.cancel()
