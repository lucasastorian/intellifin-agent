from typing import List, Optional

from schema import schema
from database import Database
from agent.action_response import ActionResponse, ActionFollowUp
from pipeline.company_provisioner import CompanyProvisioner
from agent.system_prompt import SystemPrompt
from agent.actions.base_action import BaseAction
from agent.message import Message, Action
from agent.clients.openai_client import OpenAIClient
from agent.agent_config import AgentMode, get_agent_config
from agent.actions import (ListCompaniesAction, ListFilingsAction, SearchPressReleasesAction, SearchCurrentReportsAction, SearchFilingNotesActionNew,
                           ViewFinancialStatementsAction, PythonExecAction, PlanAction, SearchFilingSectionsAction, SemanticSearchAction, )


class Agent:
    start_year: int = 2018

    def __init__(self, edgar_user_agent: str, model: str = "gpt-5", temperature: float = 1.0, max_iter: int = 20,
                 reasoning_effort: str = "medium", mode: AgentMode = AgentMode.FULL):
        self.edgar_user_agent = edgar_user_agent
        self.client = OpenAIClient(model=model, temperature=temperature, reasoning_effort=reasoning_effort)
        self.num_iter = 0
        self.max_iter = max_iter
        self.messages = []
        self._initialized = False
        self.config = get_agent_config(mode)

        self.database = Database(schema=schema, base_path="./data/intellifin.db")

    async def _initialize(self):
        """Async initialization - provisions companies database if needed"""
        if self._initialized:
            return

        provisioner = CompanyProvisioner(database=self.database, edgar_user_agent=self.edgar_user_agent)
        await provisioner.provision()
        self._initialized = True

    async def run(self, query: str) -> Optional[str]:
        """Orchestrates agent iterations (horizontal limit via max_iter)"""
        await self._initialize()

        self.messages.append(Message(role="user", status="completed", content=query))

        all_actions = [
            PlanAction(database=self.database, edgar_user_agent=self.edgar_user_agent),

            # ListCompaniesAction(database=self.database, edgar_user_agent=self.edgar_user_agent),
            # ListFilingsAction(database=self.database, edgar_user_agent=self.edgar_user_agent),
            # ReadFilingAction(database=self.database, edgar_user_agent=self.edgar_user_agent),
            #
            # SearchFilingSectionsAction(database=self.database, edgar_user_agent=self.edgar_user_agent),
            # SearchPressReleasesAction(database=self.database, edgar_user_agent=self.edgar_user_agent),
            # SearchCurrentReportsAction(database=self.database, edgar_user_agent=self.edgar_user_agent),
            # SearchFilingNotesActionNew(database=self.database, edgar_user_agent=self.edgar_user_agent),

            SemanticSearchAction(database=self.database, edgar_user_agent=self.edgar_user_agent),
            ViewFinancialStatementsAction(database=self.database, edgar_user_agent=self.edgar_user_agent),

            PythonExecAction(database=self.database, edgar_user_agent=self.edgar_user_agent)
        ]

        base_actions = all_actions
        # base_actions = [
        #     action for action in all_actions
        #     if action.__class__.__name__ in self.config.enabled_actions
        # ]

        dynamic_actions = []

        while self.num_iter < self.max_iter:
            optional_actions = await self.navigate_sequence(
                actions=base_actions + dynamic_actions,
                depth=0
            )

            if optional_actions is None:
                return self.messages[-1].content

            dynamic_actions = optional_actions
            self.num_iter += 1

        return None

    async def navigate_sequence(self, actions: List[BaseAction], allowed_actions: List[BaseAction] = None,
                                depth: int = 0, max_depth: int = 10) -> Optional[List[BaseAction]]:
        """
        Navigates through a sequence of steps with recursive forced follow-ups (vertical limit via max_depth).
        Returns optional follow-up actions to persist for next iteration.
        Returns None if terminal (no tool calls).
        """
        if depth > max_depth:
            raise RuntimeError(f"Exceeded max follow-up depth {max_depth}")

        follow_ups = await self.step(actions=actions, allowed_actions=allowed_actions)

        if follow_ups is None:
            return None

        optional_actions = []

        for follow_up in follow_ups:
            if follow_up.force:
                recursive_optional = await self.navigate_sequence(
                    actions=actions + follow_up.actions,
                    allowed_actions=follow_up.actions,
                    depth=depth + 1,
                    max_depth=max_depth
                )

                if recursive_optional:
                    optional_actions.extend(recursive_optional)
            else:
                optional_actions.extend(follow_up.actions)

        return optional_actions

    async def step(self, actions: List[BaseAction], allowed_actions: List[BaseAction] = None) -> Optional[
        List[ActionFollowUp]]:
        """Executes a single step in the agent loop"""
        # print(self.messages)
        completion = await self.client.stream(messages=self.messages, system_prompt=SystemPrompt().format(),
                                              actions=actions, allowed_actions=allowed_actions,
                                              enable_web_search=self.config.enable_web_search)
        self.messages.append(completion)
        follow_ups = await self._call_actions(completion=completion, actions=actions)

        return follow_ups

    async def _call_actions(self, completion: Message, actions: List[BaseAction]) -> Optional[List[ActionFollowUp]]:
        """Calls the relevant actions and returns whether to terminate"""
        follow_ups = []

        if not completion.actions:
            return None

        for called_action in completion.actions:
            action = self._find_action(name=called_action.name, actions=actions)

            if not action:
                self._handle_action_not_found(called_action=called_action)
            else:
                response = await action.call(action=called_action)
                self.messages.append(response.message)

                # Apply context refinement if requested
                if response.context_refinement:
                    self._apply_context_refinement(response.context_refinement)

                if response.follow_up:
                    follow_ups.append(response.follow_up)

        return follow_ups

    def _apply_context_refinement(self, refinement):
        """Refine a previous message's content by ID"""
        from agent.action_response import ContextRefinement

        for i, msg in enumerate(self.messages):
            if msg.id == refinement.message_id:
                # Refine content while preserving other fields
                self.messages[i].content = refinement.refined_content
                return

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
