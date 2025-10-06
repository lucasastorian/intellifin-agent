from typing import List, Optional

from schema import schema
from database import Database
from agent.system_prompts.system_prompt import SystemPrompt
from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.clients.openai_client import OpenAIClient
from agent.actions.search_filings import SearchFilingsAction
from agent.actions.read_filing import ReadFilingAction


class Agent:

    start_year: int = 2023
    max_iter: int = 5

    def __init__(self, edgar_user_agent: str):
        self.edgar_user_agent = edgar_user_agent
        self.client = OpenAIClient()
        self.num_iter = 0
        self.messages = []

        self.database = Database(schema=schema, base_path="./data/.local.db")

    async def run(self, query: str):
        """Runs the assistant with the given query"""
        self.messages.append(Message(role="developer", status="completed", content=SystemPrompt().format()))
        self.messages.append(Message(role="user", status="completed", content=query))

        actions = [SearchFilingsAction(database=self.database, edgar_user_agent=self.edgar_user_agent,
                                       start_year=self.start_year),
                   ReadFilingAction(database=self.database, edgar_user_agent=self.edgar_user_agent,
                                    start_year=self.start_year)]

        while self.num_iter < self.max_iter:
            terminate = await self.step(actions=actions)
            if terminate:
                break

            self.num_iter += 1

        return

    async def step(self, actions: List[BaseAction]):
        """Executes a single step in the agent loop"""
        completion = await self.client.stream(messages=self.messages)
        self.messages.append(completion)
        terminate = await self._call_actions(completion=completion, actions=actions)

        return terminate

    async def _call_actions(self, completion: Message, actions: List[BaseAction]) -> bool:
        """Calls the relevant actions and returns whether to terminate"""
        if completion.actions is None:
            return True

        for called_action in completion.actions:
            action = self._find_action(name=called_action.name, actions=actions)

            if not action:
                self._handle_action_not_found(called_action=called_action)
            else:
                message = await action.call(action=called_action)
                self.messages.append(message)

        return False

    @staticmethod
    def _find_action(name: str, actions: List[BaseAction]) -> Optional[BaseAction]:
        return next((action for action in actions if action.name == name), None)

    def _handle_action_not_found(self, called_action: Action):
        """Handles the scenario where the LLM calls a non-existent action"""
        self.messages.append(
            Message(
                role="tool",
                status="completed",
                content=f"Tool {called_action.name} is currently NOT available. Please try another approach.",
                action_id=called_action.id,
                error=True
            )
        )
