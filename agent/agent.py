from agent.system_prompts.system_prompt import SystemPrompt
from agent.message import Message, Action


class Assistant:

    max_iter: int = 5

    def __init__(self):
        self.num_iter = 0
        self.messages = []

    async def run(self, query: str):
        """Runs the assistant with the given query"""
        self.messages.append(Message(role="developer", status="completed", content=SystemPrompt().format()))
        self.messages.append(Message(role="user",  status="completed", content=query))

        while self.num_iter < self.max_iter:
            terminate = await self.step()
            if terminate:
                break

            self.num_iter += 1

        return

    async def step(self):
        """Executes a single step in the agent loop"""
        pass
