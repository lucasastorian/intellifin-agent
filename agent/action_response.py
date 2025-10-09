from typing import List
from dataclasses import dataclass

from agent.actions.base_action import BaseAction
from agent.message import Message


@dataclass
class ActionFollowUp:
    actions: List[BaseAction]
    force: bool


@dataclass
class ActionResponse:
    message: Message
    follow_up: ActionFollowUp = None
